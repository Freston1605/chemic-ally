import logging

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

logger = logging.getLogger(__name__)


class LevelListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        levels = Level.objects.filter(is_active=True)
        serializer = LevelListSerializer(levels, many=True)
        return Response(serializer.data)


class LevelDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        level = get_object_or_404(Level, slug=slug, is_active=True)
        serializer = LevelDetailSerializer(level)
        return Response(serializer.data)


class SimulateView(APIView):
    permission_classes = [AllowAny]

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
        )

        try:
            result = generate_chromatogram(
                analytes=analytes,
                column=column_config,
                mobile=mobile_config,
                operation=operation_config,
                max_pressure_bar=level.max_pressure_bar,
            )
        except Exception as e:
            logger.exception("Simulation error")
            return Response(
                {'error': 'Simulation failed', 'detail': str(e)},
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

        response_serializer = SimulationResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)

        return Response(response_serializer.data)


class ScoreSubmissionView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ScoreSubmissionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        level = get_object_or_404(Level, id=data['level_id'], is_active=True)

        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        score_record = UserScore.objects.create(
            level=level,
            session_key=session_key,
            mobile_phase=data['mobile_phase'],
            column_config=data['column_config'],
            operation_config=data['operation_config'],
            total_run_time=data['total_run_time'],
            max_pressure_bar=data['max_pressure_bar'],
            min_resolution=data['min_resolution'],
            score=data['score'],
            is_successful=data['is_successful'],
            overpressure=data['overpressure'],
        )

        progress, created = LevelProgress.objects.get_or_create(
            level=level,
            session_key=session_key,
            defaults={
                'best_score': data['score'],
                'best_resolution': data['min_resolution'],
                'best_run_time': data['total_run_time'],
                'attempts': 1,
                'completed': data['is_successful'],
            }
        )

        if not created:
            progress.attempts += 1
            if data['score'] > progress.best_score:
                progress.best_score = data['score']
            if data['min_resolution'] > progress.best_resolution:
                progress.best_resolution = data['min_resolution']
            if data['is_successful']:
                progress.completed = True
                if (
                    data['total_run_time'] < progress.best_run_time
                    or progress.best_run_time == 0
                ):
                    progress.best_run_time = data['total_run_time']
            progress.save()

        return Response(
            {'status': 'score recorded', 'score_id': score_record.id},
            status=status.HTTP_201_CREATED,
        )


class UserScoresView(APIView):
    permission_classes = [AllowAny]

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
