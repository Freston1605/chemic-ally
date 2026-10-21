import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from .models import Level, UserScore, LevelProgress
from .serializers import (
    LevelListSerializer,
    LevelDetailSerializer,
    SimulationRequestSerializer,
    SimulationResponseSerializer,
    ScoreSubmissionSerializer,
    UserScoreSerializer,
    LevelProgressSerializer,
)
from .simulation.engine import (
    AnalyteProperties,
    ColumnConfig,
    MobilePhaseConfig,
    OperationConfig,
    generate_chromatogram,
    calculate_score,
)
from .tokens import (
    hash_result_token,
    sign_result_token,
    verify_result_token,
)

logger = logging.getLogger(__name__)

# One response body for EVERY rejection in ScoreSubmissionView (D022).
#
# Tamper, expiry, replay, level mismatch and out-of-range metrics are all
# indistinguishable to the client: a caller who can tell "bad signature" from
# "good signature, impossible numbers" learns which part of a forgery to fix
# next. The real reason is logged server-side instead (see ``_reject_token``),
# so CloudWatch operators keep full diagnostic detail while the response
# discloses nothing -- no field name, no expected value, no stack trace.
SCORE_REJECTION_ERROR = 'Invalid or expired result token'


def _reject_token(reason):
    """Log ``reason`` and return the single non-disclosing 400 response.

    ``reason`` is for operators only. It deliberately never reaches the
    response body or the logs' client-visible surface.
    """
    logger.warning('Score submission rejected: %s', reason)
    return Response(
        {'error': SCORE_REJECTION_ERROR},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _range_check_signed_metrics(payload, level):
    """Return a rejection reason for out-of-range signed metrics, or None.

    These numbers arrived inside an authentic signature, so this is not a
    forgery check: it is the check that the SERVER, not the client, decides
    what a legitimate run of this level can look like. Structure mirrors the
    model's own invariants (``UserScore.clean`` and the non-negative
    ``CheckConstraint``s) so a signed-but-impossible result is refused with
    the uniform 400 instead of tripping a 500 or a DB constraint later.

    ``total_run_time`` must be strictly positive because it is the divisor in
    :func:`calculate_score` and the model's ``clean()`` rejects zero.
    ``max_pressure_bar`` must not exceed the level's own limit: a run above
    the column limit is not a legitimate result of this level, and a signed
    ``overpressure`` flag must not be able to launder it into an accepted one.
    """
    total_run_time = payload['total_run_time']
    max_pressure_bar = payload['max_pressure_bar']
    min_resolution = payload['min_resolution']
    critical_pair = payload['critical_pair']

    if not isinstance(total_run_time, (int, float)) or total_run_time <= 0:
        return 'total_run_time out of range'
    if not isinstance(min_resolution, (int, float)) or min_resolution < 0:
        return 'min_resolution out of range'
    if not isinstance(max_pressure_bar, (int, float)) or max_pressure_bar < 0:
        return 'max_pressure_bar out of range'
    if max_pressure_bar > level.max_pressure_bar:
        return 'max_pressure_bar exceeds level limit'
    if critical_pair is not None:
        if not isinstance(critical_pair, list) or len(critical_pair) > 2:
            return 'critical_pair is not a pair'
        if any(not isinstance(name, str) for name in critical_pair):
            return 'critical_pair members are not strings'
    return None


class LevelListView(APIView):
    permission_classes = [AllowAny]
    # No throttle_scope on purpose (M006/S02): listing the levels is a cheap
    # read and stays available to every visitor.

    def get(self, request):
        levels = Level.objects.filter(is_active=True)
        serializer = LevelListSerializer(levels, many=True)
        return Response(serializer.data)


class LevelDetailView(APIView):
    permission_classes = [AllowAny]
    # No throttle_scope on purpose (M006/S02): a cheap read.

    def get(self, request, slug):
        level = get_object_or_404(Level, slug=slug, is_active=True)
        serializer = LevelDetailSerializer(level)
        return Response(serializer.data)


class SimulateView(APIView):
    permission_classes = [AllowAny]
    # M006/S02 (R028): the simulation is the CPU-bound endpoint -- it runs the
    # full LSS/gradient engine for every request -- so it gets its OWN
    # throttle scope and its own budget. A flood here must not consume the
    # scores endpoint's allowance (separate scopes, separate limits).
    throttle_scope = 'simulate'

    def post(self, request):
        serializer = SimulationRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        level_id = data['level_id']
        level = get_object_or_404(Level, id=level_id, is_active=True)

        analytes = []
        for analyte in level.analytes.all():
            analytes.append(AnalyteProperties(
                name=analyte.name,
                log_kw=analyte.log_kw,
                s_parameter=analyte.s_parameter,
                pka=analyte.pka,
                neutral_charge=analyte.neutral_charge,
                concentration_mm=analyte.concentration_mm,
                extinction_coefficient=analyte.extinction_coefficient,
            ))

        mobile_config = MobilePhaseConfig(
            start_b=data['mobile_phase']['start_b'],
            end_b=data['mobile_phase']['end_b'],
            ramp_time=data['mobile_phase']['ramp_time'],
            ph=data['mobile_phase']['ph'],
            buffer_concentration_mm=data['mobile_phase']['buffer_concentration_mm'],
            is_gradient=data['mobile_phase'].get('is_gradient', True),
        )

        column_config = ColumnConfig(
            chemistry=data['column']['chemistry'],
            length_mm=data['column']['length_mm'],
            id_mm=data['column']['id_mm'],
            particle_size_um=data['column']['particle_size_um'],
        )

        operation_config = OperationConfig(
            flow_rate_ml_min=data['operation']['flow_rate_ml_min'],
            temperature_c=data['operation']['temperature_c'],
            injection_volume_ul=data['operation']['injection_volume_ul'],
            dwell_time_min=data['operation'].get('dwell_time_min', 1.0),
        )

        try:
            result = generate_chromatogram(
                analytes=analytes,
                column=column_config,
                mobile=mobile_config,
                operation=operation_config,
                max_pressure_bar=level.max_pressure_bar,
            )
        except Exception:
            logger.exception("Simulation error")
            return Response(
                {'error': 'Simulation failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        score, is_successful = calculate_score(
            total_run_time=result.total_run_time,
            min_resolution=result.min_resolution,
            max_pressure_bar=result.max_pressure_bar,
            pressure_limit=level.max_pressure_bar,
            base_points=level.base_points,
        )

        critical_pair = None
        if result.critical_pair:
            critical_pair = list(result.critical_pair)

        response_data = {
            'chromatogram': {
                'time': result.time.tolist(),
                'signal': result.signal.tolist(),
            },
            'peaks': [
                {
                    'analyte': p.analyte,
                    'retention_time': p.retention_time,
                    'width': p.width,
                    'height': p.height,
                    'area': p.area,
                }
                for p in result.peaks
            ],
            'metrics': {
                'total_run_time': result.total_run_time,
                'max_pressure_bar': result.max_pressure_bar,
                'min_resolution': result.min_resolution,
                'critical_pair': critical_pair,
            },
            'score': score,
            'success': is_successful,
            'overpressure': result.overpressure,
            'message': (
                'Run complete' if not result.overpressure
                else 'System overpressure!'
            ),
        }

        # R013/D021: hand the client a server-signed summary of the run the
        # server just computed. The token is built from the values this view
        # produced -- notably ``level.id`` (the resolved, active level) rather
        # than the raw request input -- so the scores endpoint later verifies
        # numbers it can trust. The score is deliberately NOT signed: the
        # server recomputes it from these metrics, and signing it would create
        # a second source of truth.
        response_data['result_token'] = sign_result_token({
            'level_id': level.id,
            'total_run_time': result.total_run_time,
            'max_pressure_bar': result.max_pressure_bar,
            'min_resolution': result.min_resolution,
            'critical_pair': critical_pair,
            'overpressure': result.overpressure,
        })

        # The serializer validates exactly what it will return, so the token is
        # passed through it (and therefore appears in ``.data`` in declared
        # field order) rather than bolted onto the response afterwards.
        response_serializer = SimulationResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)

        return Response(response_serializer.data)


class ScoreSubmissionView(APIView):
    permission_classes = [AllowAny]
    # M006/S02 (R028): the write-heavy endpoint (one row per accepted
    # submission) gets its own scope and the tighter of the two limits.
    throttle_scope = 'scores'

    def post(self, request):
        """Record a run whose numbers came from a server-signed result token.

        Fixed order (D022) -- the token is verified BEFORE anything touches the
        database. Under D006 the Level lookup raises ``OperationalError``
        outside this view's try/except and the project's DRF handler maps it to
        a 503, so a forged token must be refused while the request is still
        DB-free; otherwise a tamper probe in production would observe 503
        instead of the required 400.
        """
        # (a) Verify the signature first. ``request.data`` is untrusted and may
        # not even be a mapping, so the access itself is guarded.
        result_token = None
        if isinstance(request.data, dict):
            result_token = request.data.get('result_token')
        payload = verify_result_token(result_token)
        if payload is None:
            return _reject_token('invalid or expired token')

        # (b) ``level_id`` stays required but UNTRUSTED: it is the level the
        # client believes it rendered. The signed value is authoritative, and
        # a disagreement means the client is submitting against the wrong
        # level -- refuse rather than silently scoring the signed one.
        raw_level_id = None
        if isinstance(request.data, dict):
            raw_level_id = request.data.get('level_id')
        try:
            submitted_level_id = int(raw_level_id)
        except (TypeError, ValueError):
            return _reject_token('level_id missing or not an integer')
        if submitted_level_id != payload['level_id']:
            return _reject_token('level mismatch')

        # (c) 404 for an unknown/inactive level: not a forgery signal, and the
        # pre-existing contract for this endpoint.
        level = get_object_or_404(
            Level, id=payload['level_id'], is_active=True
        )

        # (d) Range-check the signed metrics before any write.
        reason = _range_check_signed_metrics(payload, level)
        if reason is not None:
            return _reject_token(reason)

        # (e) Defensive redundancy: step (d) already makes this unreachable,
        # but it keeps the model's ``clean()`` invariant unviolatable if the
        # range check is ever loosened.
        overpressure = bool(payload['overpressure'])
        if payload['max_pressure_bar'] > level.max_pressure_bar and not overpressure:
            return _reject_token('overpressure flag missing')

        serializer = ScoreSubmissionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data

        # (f) THE authoritative values: recomputed by the server from the
        # signed metrics. ``data['score']`` / ``data['is_successful']`` are
        # still accepted by the serializer for backward compatibility and are
        # never read -- that is the entire point of R014.
        score, is_successful = calculate_score(
            total_run_time=payload['total_run_time'],
            min_resolution=payload['min_resolution'],
            max_pressure_bar=payload['max_pressure_bar'],
            pressure_limit=level.max_pressure_bar,
            base_points=level.base_points,
        )

        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        # (h) Replay guard. The pre-check is the fast path; the unique column
        # (S05-T04) is the real guarantee, and the IntegrityError branch below
        # closes the check-then-insert race.
        result_token_hash = hash_result_token(result_token)
        if UserScore.objects.filter(
            result_token_hash=result_token_hash
        ).exists():
            return _reject_token('replayed token')

        # (g) The config objects stay descriptive request metadata: they are
        # what the model's ``clean()`` validates and are never derived from the
        # token (the token carries the result, not the request shape).
        score_record = UserScore(
            level=level,
            session_key=session_key,
            mobile_phase=data['mobile_phase'],
            column_config=data['column_config'],
            operation_config=data['operation_config'],
            total_run_time=payload['total_run_time'],
            max_pressure_bar=payload['max_pressure_bar'],
            min_resolution=payload['min_resolution'],
            critical_pair=payload['critical_pair'],
            score=score,
            is_successful=is_successful,
            overpressure=overpressure,
            result_token_hash=result_token_hash,
        )
        try:
            score_record.full_clean()
        except ValidationError as e:
            return Response(
                {'errors': e.message_dict},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            score_record.save()
        except IntegrityError:
            # A genuine constraint bug must stay visible (D010): only a row
            # that now holds this hash is a replay; anything else re-raises.
            if UserScore.objects.filter(
                result_token_hash=result_token_hash
            ).exists():
                return _reject_token('replayed token')
            raise

        progress, created = LevelProgress.objects.get_or_create(
            level=level,
            session_key=session_key,
            defaults={
                'best_score': score,
                'best_resolution': payload['min_resolution'],
                'best_run_time': payload['total_run_time'],
                'attempts': 1,
                'completed': is_successful,
            }
        )

        if not created:
            progress.attempts += 1
            if score > progress.best_score:
                progress.best_score = score
            if payload['min_resolution'] > progress.best_resolution:
                progress.best_resolution = payload['min_resolution']
            if is_successful:
                progress.completed = True
                if (
                    payload['total_run_time'] < progress.best_run_time
                    or progress.best_run_time == 0
                ):
                    progress.best_run_time = payload['total_run_time']
            progress.save()

        return Response(
            {'status': 'score recorded', 'score_id': score_record.id},
            status=status.HTTP_201_CREATED,
        )


class UserScoresView(APIView):
    permission_classes = [AllowAny]
    # No throttle_scope on purpose (M006/S02): a cheap read of the caller's
    # own rows.

    def get(self, request):
        session_key = request.session.session_key
        if not session_key:
            return Response([])

        scores = UserScore.objects.filter(
            session_key=session_key,
        ).order_by('-created_at')
        serializer = UserScoreSerializer(scores, many=True)
        return Response(serializer.data)


class LevelProgressView(APIView):
    permission_classes = [AllowAny]
    # No throttle_scope on purpose (M006/S02, R007): the progress endpoint is
    # read-only and must keep answering 200 for the caller's own rows.

    def get(self, request):
        session_key = request.session.session_key
        if not session_key:
            return Response([])

        progress = LevelProgress.objects.filter(session_key=session_key)
        serializer = LevelProgressSerializer(progress, many=True)
        return Response(serializer.data)


class SimulatorIndexView(TemplateView):
    template_name = 'hplc_simulator/index.html'


class SimulatorDetailView(TemplateView):
    template_name = 'hplc_simulator/simulator.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['level_slug'] = kwargs.get('slug', '')
        return context
