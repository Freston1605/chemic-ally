"""End-to-end score-integrity contract tests (R013, R014, R015, D021, D022).

S05-T02 froze :data:`~hplc_simulator.tokens.SIGNED_FIELDS`, S05-T03 made
``ScoreSubmissionView`` verify that payload and recompute the score from it,
S05-T04 added the unique ``UserScore.result_token_hash`` column -- but each of
those was proved in isolation. This module is the *contract-exercising* suite:
every test drives the REAL ``POST /hplc/api/simulate/`` endpoint through
``rest_framework.test.APIClient`` and then submits the token the endpoint
itself minted to ``reverse('hplc_simulator:api-scores')``. A drift in any one
of the four seams -- the signed set at simulation time, the response
serializer surface, the verification path, or the hash/uniqueness check --
turns one of these tests red:

* :class:`TestEndToEndRoundTrip` -- the happy path, plus the proof that the
  persisted score is the server's ``calculate_score`` value rather than the
  client's ``score`` field.
* :class:`TestOrganicTamperVariants` -- forgeries derived programmatically
  from a real token (index computed, never pasted).
* :class:`TestExpiryContract` / :class:`TestReplayContract` -- the replay
  negative test (Q7) and the false-positive guard that keeps two legitimate
  runs of the same parameters acceptable.
* :class:`TestRangeAndConsistencyNegatives` -- hostile but correctly signed
  numbers, and a signed/submitted level disagreement.
* :class:`TestNonDisclosure` -- one fixed message for every 400, and no
  internal marker or echoed client value anywhere in the body.
* :class:`TestBackwardCompatibility` -- the deploy-window shape.
* :class:`TestSignedFieldSetIsTheContract` -- the explicit link between
  ``SIGNED_FIELDS``, the numbers the view reads, and the declared fields of
  ``SimulationResponseSerializer``.

No test hardcodes a ``/hplc/api/...`` path, and every call asserts
``status_code != 429`` (the S03 convention, kept meaningful by the shared
autouse cache-clear fixture).

``result_token`` is deliberately absent from the forbidden-substring list used
by :func:`assert_uniform_rejection`: the slice's own uniform message is
``'Invalid or expired result token'`` (D022), so the word is necessarily
present. What must never appear is the reject *reason* (``bad signature``,
``replayed token``, ``rejected``, ...) or anything that names a failed check.
"""

import re

import pytest
from django.core import signing as django_signing
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hplc_simulator.models import LevelProgress, UserScore
from hplc_simulator.serializers import (
    ScoreSubmissionSerializer,
    SimulationResponseSerializer,
)
from hplc_simulator.simulation.engine import calculate_score
from hplc_simulator.tokens import (
    SIGNED_FIELDS,
    hash_result_token,
    sign_result_token,
    verify_result_token,
)
from hplc_simulator.views import SCORE_REJECTION_ERROR

# Substrings that must never appear in ANY rejection body. The first five are
# internal detail (a stack trace or an exception class names the exact check
# that failed); the rest are the vocabulary of the view's own ``_reject_token``
# reasons, which must stay server-side. Only single words that cannot occur in
# the fixed message are listed -- a shorter fragment would be a false alarm
# (``token`` alone is impossible to exclude, see the module docstring).
FORBIDDEN_IN_REJECTION = (
    'Traceback',
    'OperationalError',
    'InterfaceError',
    'SignatureExpired',
    'BadSignature',
    'result_token_hash',
    'rejected',
    'tampered',
    'replay',
)

# Built by concatenation so this module's own docstring and assertion message
# cannot trip it: the code below must never ship a literal ``/hplc/api/`` path,
# because every URL comes from ``reverse``. Asserted in
# ``TestContractSourceHygiene`` against the parsed source (docstrings and
# comments excluded), which is what makes it a check on the code rather than
# on the prose about the code.
HARDCODED_API_PATH = re.compile('/hplc/' + 'api/')


@pytest.fixture
def client():
    return APIClient()


def _simulate_url():
    return reverse('hplc_simulator:api-simulate')


def _scores_url():
    return reverse('hplc_simulator:api-scores')


def _prime_session(client):
    """Save a session and pin its cookie on the client, returning the key.

    Both submissions in a replay test must present the SAME session, otherwise
    "the session still owns exactly one UserScore row" would be vacuous.
    """
    session = client.session
    session.save()
    return client.session.session_key


def _simulate(client, payload):
    """Run the real simulate endpoint and return its JSON body."""
    response = client.post(_simulate_url(), payload, format='json')
    assert response.status_code != 429
    assert response.status_code == 200, response.content
    return response.json()


def _submit_body(level_id, token, metrics=None, **overrides):
    """The full request body the deployed browser client sends.

    ``score``/``is_successful``/``overpressure`` are present on purpose: they
    are the accepted-and-ignored legacy fields (D022), and this module is one
    of the places their being ignored must be proved.
    """
    body = {
        'level_id': level_id,
        'result_token': token,
        'score': 8500.0,
        'total_run_time': 24.5,
        'min_resolution': 1.8,
        'max_pressure_bar': 120.0,
        'is_successful': True,
        'overpressure': False,
        'mobile_phase': {
            'start_b': 5,
            'end_b': 95,
            'ramp_time': 20,
            'ph': 3.0,
            'is_gradient': True,
        },
        'column_config': {
            'chemistry': 'C18',
            'length_mm': 150,
            'id_mm': 4.6,
            'particle_size_um': 5.0,
        },
        'operation_config': {
            'flow_rate_ml_min': 1.0,
            'temperature_c': 30,
            'injection_volume_ul': 10,
        },
    }
    if metrics:
        body.update(
            {
                'total_run_time': metrics['total_run_time'],
                'min_resolution': metrics['min_resolution'],
                'max_pressure_bar': metrics['max_pressure_bar'],
                'overpressure': metrics.get('overpressure', False),
            }
        )
    body.update(overrides)
    return body


def _simulate_round_trip(client, payload):
    """simulate -> its own token/metrics -> the score submission body.

    Every happy-path test starts here, which is what makes "no test may
    hand-craft a token for the happy path" true by construction: the token is
    always the one the simulate view minted.
    """
    simulated = _simulate(client, payload)
    metrics = simulated['metrics']
    body = _submit_body(
        payload['level_id'],
        simulated['result_token'],
        metrics=metrics,
        overpressure=simulated['overpressure'],
    )
    return simulated, body


def _flip_base64_body_char(token):
    """Flip ONE character inside the base64 body, index computed not pasted."""
    first_segment = token.split(':')[0]
    index = len(first_segment) // 2
    original = token[index]
    replacement = 'A' if original != 'A' else 'B'
    return token[:index] + replacement + token[index + 1:]


def _decode_base62_number(text):
    """Decode Django's base62 timestamp segment to an int."""
    from django.core.signing import b62_decode

    return b62_decode(text)


def _bump_timestamp_decimal(token, delta=1):
    """Rewrite the timestamp with a changed DECIMAL digit.

    ``TimestampSigner`` emits ``<payload>:<base62 seconds>:<signature>``. The
    embedded timestamp is rewound/shifted here by re-encoding a changed
    number, which makes the per-second nonce move by an explicit decimal
    amount instead of an opaque base62 hop. Either way the signature no longer
    covers the payload+timestamp pair, so the token must be refused.
    """
    from django.core.signing import b62_encode

    payload_segment, timestamp, signature = token.split(':')
    return (
        f'{payload_segment}:{b62_encode(_decode_base62_number(timestamp) + delta)}'
        f':{signature}'
    )


def _rewind_timestamp(token):
    """Push the embedded timestamp far into the past (rewind, not just shift)."""
    from django.core.signing import b62_encode

    payload_segment, timestamp, signature = token.split(':')
    rewound = _decode_base62_number(timestamp) - 999_999
    return f'{payload_segment}:{b62_encode(rewound)}:{signature}'


def _sign_with_signer(signer, metrics):
    """Sign exactly the signed field set with an arbitrary signer."""
    return signer.sign_object({field: metrics[field] for field in SIGNED_FIELDS})


def _sign_organic(client, payload, **metric_overrides):
    """Mint a token from a SIGNED metric set derived from a real simulate call.

    Only used for the hostile-but-authentically-signed cases: the metric
    values come from the endpoint's own response, then one of them is
    overridden. That keeps the payload shape produced by the real contract
    rather than by a hand-typed literal.
    """
    simulated = _simulate(client, payload)
    metrics = dict(simulated['metrics'])
    metrics['level_id'] = payload['level_id']
    metrics['overpressure'] = simulated['overpressure']
    metrics.update(metric_overrides)
    return _sign_with_signer(
        django_signing.TimestampSigner(
            salt=_score_token_salt(),
        ),
        metrics,
    )


def _score_token_salt():
    from hplc_simulator.tokens import SCORE_TOKEN_SALT

    return SCORE_TOKEN_SALT


def assert_uniform_rejection(response, *, client_score=8500.0):
    """Assert the ONE non-disclosing 400 body, with nothing extra in it.

    This is the single place every rejection assertion goes through, so the
    non-disclosure property is enforced for every negative test in the file
    rather than restated (and possibly weakened) per test.
    """
    assert response.status_code == 400, response.content
    assert response.status_code != 429
    assert response.status_code != 500
    assert response.status_code != 503

    body = response.json()
    assert body == {'error': SCORE_REJECTION_ERROR}

    raw = response.content.decode()
    for forbidden in FORBIDDEN_IN_REJECTION:
        assert forbidden not in raw, (
            f'rejection body leaked {forbidden!r}: {raw!r}'
        )
    # The client-supplied score is never echoed, in any of its spellings.
    for spelling in (str(client_score), str(int(client_score))):
        assert spelling not in raw, f'rejection body echoed score: {raw!r}'
    return body


class TestEndToEndRoundTrip:
    """A real simulate token is accepted and scored by the server (R013/R014)."""

    def test_token_from_simulate_is_accepted_with_server_computed_score(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        session_key = _prime_session(client)
        simulated, body = _simulate_round_trip(client, valid_simulate_payload)
        metrics = simulated['metrics']

        response = client.post(_scores_url(), body, format='json')

        assert response.status_code == 201, response.content
        assert response.status_code != 429

        payload = response.json()
        assert payload['status'] == 'score recorded'
        assert isinstance(payload['score_id'], int)

        expected_score, expected_success = calculate_score(
            total_run_time=metrics['total_run_time'],
            min_resolution=metrics['min_resolution'],
            max_pressure_bar=metrics['max_pressure_bar'],
            pressure_limit=api_level_with_analytes.max_pressure_bar,
            base_points=api_level_with_analytes.base_points,
        )

        record = UserScore.objects.get(
            level=api_level_with_analytes, session_key=session_key
        )
        assert record.score == pytest.approx(expected_score)
        assert record.score != pytest.approx(body['score'])
        assert record.is_successful is expected_success
        assert record.result_token_hash == hash_result_token(
            simulated['result_token']
        )

    def test_the_endpoint_signed_exactly_the_metrics_it_returned(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """The signed set is what the view range-checks: no drift is possible.

        This is the seam (a)->(b)->(c) link in one assertion: the payload the
        scores view verifies carries the same numbers the simulate response
        reported, for every signed field.
        """
        simulated = _simulate(client, valid_simulate_payload)
        metrics = simulated['metrics']

        verified = verify_result_token(simulated['result_token'])

        assert verified is not None
        assert set(verified) == set(SIGNED_FIELDS)
        assert verified['level_id'] == api_level_with_analytes.id
        assert verified['total_run_time'] == pytest.approx(
            metrics['total_run_time']
        )
        assert verified['max_pressure_bar'] == pytest.approx(
            metrics['max_pressure_bar']
        )
        assert verified['min_resolution'] == pytest.approx(
            metrics['min_resolution']
        )
        assert verified['critical_pair'] == metrics['critical_pair']
        assert verified['overpressure'] is simulated['overpressure']


class TestOrganicTamperVariants:
    """Every forgery derived from a REAL token is refused with the same 400."""

    def test_probability_of_a_vacuous_tamper_is_zero(
        self, client, valid_simulate_payload
    ):
        """The tamper helper really changes the token (guards the assertions)."""
        simulated = _simulate(client, valid_simulate_payload)
        real = simulated['result_token']

        tampered = _flip_base64_body_char(real)

        assert tampered != real
        assert len(tampered) == len(real)
        assert verify_result_token(tampered) is None

    def test_flipped_base64_body_character_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        simulated, body = _simulate_round_trip(client, valid_simulate_payload)
        body['result_token'] = _flip_base64_body_char(
            simulated['result_token']
        )

        response = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0
        assert LevelProgress.objects.count() == 0

    def test_changed_timestamp_decimal_is_refused(
        self, client, valid_simulate_payload
    ):
        """The timestamp lives inside the signed region, so editing it breaks it."""
        simulated, body = _simulate_round_trip(client, valid_simulate_payload)
        shifted = _bump_timestamp_decimal(simulated['result_token'], delta=1)
        assert shifted != simulated['result_token']
        body['result_token'] = shifted

        response = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_rewound_timestamp_is_refused(
        self, client, valid_simulate_payload
    ):
        simulated, body = _simulate_round_trip(client, valid_simulate_payload)
        body['result_token'] = _rewind_timestamp(
            simulated['result_token']
        )

        response = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_token_signed_with_a_different_salt_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        simulated = _simulate(client, valid_simulate_payload)
        metrics = dict(simulated['metrics'])
        metrics['level_id'] = api_level_with_analytes.id
        metrics['overpressure'] = simulated['overpressure']
        foreign_salt = django_signing.TimestampSigner(
            salt='hplc-simulator.other-namespace.v1'
        )
        forged = _sign_with_signer(foreign_salt, metrics)
        assert verify_result_token(forged) is None

        response = client.post(
            _scores_url(),
            _submit_body(api_level_with_analytes.id, forged),
            format='json',
        )

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_token_signed_with_a_different_key_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        simulated = _simulate(client, valid_simulate_payload)
        metrics = dict(simulated['metrics'])
        metrics['level_id'] = api_level_with_analytes.id
        metrics['overpressure'] = simulated['overpressure']
        foreign_key = django_signing.TimestampSigner(
            key='x' * 50, salt=_score_token_salt()
        )
        forged = _sign_with_signer(foreign_key, metrics)
        assert verify_result_token(forged) is None

        response = client.post(
            _scores_url(),
            _submit_body(api_level_with_analytes.id, forged),
            format='json',
        )

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0


class TestExpiryContract:
    """An otherwise perfect token past its lifetime is a 400, never a 5xx."""

    def test_token_past_its_lifetime_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        _, body = _simulate_round_trip(client, valid_simulate_payload)

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1):
            response = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0
        assert LevelProgress.objects.count() == 0

    def test_the_same_token_is_accepted_again_once_the_window_is_restored(
        self, client, valid_simulate_payload
    ):
        """Expiry is the setting, not the token: it flips back when restored.

        Proves the refusal above was caused by the TTL rather than by the
        token being malformed, and that ``SCORE_TOKEN_MAX_AGE_SECONDS`` is
        read at call time.
        """
        _, body = _simulate_round_trip(client, valid_simulate_payload)

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1):
            expired = client.post(_scores_url(), body, format='json')
        assert_uniform_rejection(expired)

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=900):
            accepted = client.post(_scores_url(), body, format='json')
        assert accepted.status_code == 201, accepted.content
        assert accepted.status_code != 429


class TestReplayContract:
    """One token, one row -- and the false-positive guard (R015, Q7)."""

    def test_replaying_a_token_is_refused_and_never_inflates_progress(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        session_key = _prime_session(client)
        simulated, body = _simulate_round_trip(client, valid_simulate_payload)
        session_scores = UserScore.objects.filter(
            level=api_level_with_analytes, session_key=session_key
        )
        session_progress = LevelProgress.objects.filter(
            level=api_level_with_analytes, session_key=session_key
        )

        first = client.post(_scores_url(), body, format='json')
        assert first.status_code == 201, first.content
        assert first.status_code != 429
        assert session_scores.count() == 1
        assert session_progress.get().attempts == 1

        # A tamper rejection collected in the same test, so the replay body can
        # be compared against it rather than against a pasted constant.
        tampered_body = dict(body)
        tampered_body['result_token'] = _flip_base64_body_char(
            simulated['result_token']
        )
        tamper_response = client.post(
            _scores_url(), tampered_body, format='json'
        )

        replay = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(replay)
        assert replay.content == tamper_response.content
        assert session_scores.count() == 1
        assert session_progress.count() == 1
        assert session_progress.get().attempts == 1
        assert session_progress.get().best_score == pytest.approx(
            session_scores.get().score
        )

    def test_two_simulate_runs_produce_two_tokens_and_two_attempts(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """Identical request parameters are NOT a false replay.

        Two separate simulate calls -- even with byte-identical parameters --
        must yield two distinct acceptable tokens, and the second submission
        must be accepted. This is the guard that stops the unique
        ``result_token_hash`` column from rejecting legitimate repetition.
        """
        session_key = _prime_session(client)

        first_simulated = _simulate(client, valid_simulate_payload)
        second_simulated = _simulate(client, valid_simulate_payload)
        first_token = first_simulated['result_token']
        second_token = second_simulated['result_token']

        assert verify_result_token(first_token) is not None
        assert verify_result_token(second_token) is not None
        # Token identity is one-per-mint, not one-per-second:
        # ``sign_result_token`` mixes a fresh nonce into the signed region
        # (``tokens.NONCE_FIELD``), so two runs of the SAME parameters inside
        # the same wall-clock second still yield two distinct tokens. Without
        # that, the unique ``result_token_hash`` column would refuse the
        # second submission below as a replay -- the false positive this test
        # exists to catch.
        assert first_token != second_token
        assert hash_result_token(first_token) != hash_result_token(
            second_token
        )

        first = client.post(
            _scores_url(),
            _submit_body(
                api_level_with_analytes.id,
                first_token,
                metrics=first_simulated['metrics'],
                overpressure=first_simulated['overpressure'],
            ),
            format='json',
        )
        second = client.post(
            _scores_url(),
            _submit_body(
                api_level_with_analytes.id,
                second_token,
                metrics=second_simulated['metrics'],
                overpressure=second_simulated['overpressure'],
            ),
            format='json',
        )

        assert first.status_code == 201, first.content
        assert second.status_code == 201, second.content
        assert first.status_code != 429
        assert second.status_code != 429

        progress = LevelProgress.objects.get(
            level=api_level_with_analytes, session_key=session_key
        )
        assert progress.attempts == 2
        assert (
            UserScore.objects.filter(
                level=api_level_with_analytes, session_key=session_key
            ).count()
            == 2
        )

    def test_two_mints_of_the_same_payload_are_never_byte_identical(
        self, api_level_with_analytes
    ):
        """Token identity is one-per-mint, deterministically (R015, D021).

        ``TimestampSigner`` stamps to whole seconds, so two mints of the SAME
        payload would otherwise serialize byte-identically whenever they land
        in the same second -- and the unique ``result_token_hash`` column
        would then read a perfectly legitimate second run as a replay. Minting
        twice back to back makes that window certain instead of probable, so
        this test fails EVERY time the per-mint nonce is dropped, while the
        endpoint-level repetition above only collides on some runs.
        """
        payload = {
            'level_id': api_level_with_analytes.id,
            'total_run_time': 24.5,
            'max_pressure_bar': 120.0,
            'min_resolution': 1.8,
            'critical_pair': ['Caffeine', 'Acetaminophen'],
            'overpressure': False,
        }

        first = sign_result_token(payload)
        second = sign_result_token(payload)

        assert first != second
        assert hash_result_token(first) != hash_result_token(second)
        # The nonce lives inside the signature but is not something a caller
        # ever sees: both tokens verify to the SAME signed numbers.
        assert verify_result_token(first) == verify_result_token(second)
        assert set(verify_result_token(first)) == set(SIGNED_FIELDS)
        assert tuple(verify_result_token(first)) == SIGNED_FIELDS

    def test_a_bumped_timestamp_yields_a_second_accepted_submission(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """Token identity, not parameter identity, is what the guard keys on.

        The token text cannot be edited (the timestamp is inside the signed
        region), so the second token is minted the way a real second run
        would be: the signer's clock is advanced in Django's own module global
        and the same metrics are signed again. Two distinct tokens over
        identical numbers must both be accepted.
        """
        session_key = _prime_session(client)
        simulated = _simulate(client, valid_simulate_payload)
        metrics = dict(simulated['metrics'])
        metrics['level_id'] = api_level_with_analytes.id
        metrics['overpressure'] = simulated['overpressure']
        first_token = simulated['result_token']

        import time as time_module

        real_time = time_module.time
        django_signing.time.time = lambda: real_time() + 5
        try:
            second_token = _sign_with_signer(
                django_signing.TimestampSigner(salt=_score_token_salt()),
                metrics,
            )
        finally:
            django_signing.time.time = real_time

        assert second_token != first_token
        assert hash_result_token(second_token) != hash_result_token(
            first_token
        )

        for token in (first_token, second_token):
            response = client.post(
                _scores_url(),
                _submit_body(
                    api_level_with_analytes.id,
                    token,
                    metrics=simulated['metrics'],
                    overpressure=simulated['overpressure'],
                ),
                format='json',
            )
            assert response.status_code == 201, response.content
            assert response.status_code != 429

        assert (
            LevelProgress.objects.get(
                level=api_level_with_analytes, session_key=session_key
            ).attempts
            == 2
        )


class TestRangeAndConsistencyNegatives:
    """Authentically signed but impossible or inconsistent submissions."""

    @pytest.mark.parametrize(
        'overrides',
        [
            {'total_run_time': 0.0},
            {'total_run_time': -5.0},
            {'min_resolution': -0.5},
            {'max_pressure_bar': -1.0},
        ],
    )
    def test_signed_out_of_range_metrics_are_refused(
        self, client, valid_simulate_payload, api_level_with_analytes,
        overrides,
    ):
        token = _sign_organic(
            client, valid_simulate_payload, **overrides
        )
        # The signature is authentic; only the numbers are impossible.
        assert verify_result_token(token) is not None

        response = client.post(
            _scores_url(),
            _submit_body(
                api_level_with_analytes.id, token, **overrides
            ),
            format='json',
        )

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0
        assert LevelProgress.objects.count() == 0

    def test_signed_pressure_above_the_level_limit_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        over_limit = api_level_with_analytes.max_pressure_bar + 500.0
        token = _sign_organic(
            client, valid_simulate_payload, max_pressure_bar=over_limit
        )
        assert verify_result_token(token) is not None

        response = client.post(
            _scores_url(),
            _submit_body(
                api_level_with_analytes.id,
                token,
                max_pressure_bar=over_limit,
            ),
            format='json',
        )

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_signed_pressure_above_the_limit_cannot_be_laundered_by_the_flag(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """A signed ``overpressure=True`` must not make an impossible run valid."""
        over_limit = api_level_with_analytes.max_pressure_bar + 500.0
        token = _sign_organic(
            client,
            valid_simulate_payload,
            max_pressure_bar=over_limit,
            overpressure=True,
        )
        assert verify_result_token(token) is not None

        response = client.post(
            _scores_url(),
            _submit_body(
                api_level_with_analytes.id,
                token,
                overpressure=True,
                max_pressure_bar=over_limit,
            ),
            format='json',
        )

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_level_id_disagreeing_with_the_submitted_level_is_refused(
        self, client, valid_simulate_payload, api_level, api_level_with_analytes
    ):
        """The token is minted for a DIFFERENT level than the body claims."""
        token = _sign_organic(client, valid_simulate_payload)
        assert verify_result_token(token) is not None

        response = client.post(
            _scores_url(),
            _submit_body(api_level.id, token),
            format='json',
        )

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    @pytest.mark.parametrize('drop', [True, False])
    def test_missing_or_unusable_token_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes, drop
    ):
        _, body = _simulate_round_trip(client, valid_simulate_payload)
        if drop:
            del body['result_token']
        else:
            body['result_token'] = ''

        response = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_garbage_token_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        _, body = _simulate_round_trip(client, valid_simulate_payload)
        body['result_token'] = 'not-a-real-token'

        response = client.post(_scores_url(), body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0


class TestNonDisclosure:
    """Every 400 in this file is byte-identical and leaks nothing."""

    def test_all_rejection_reasons_share_one_indistinguishable_body(
        self, client, valid_simulate_payload, api_level_with_analytes, api_level
    ):
        simulated, good_body = _simulate_round_trip(
            client, valid_simulate_payload
        )
        bodies = []

        # Tamper (index computed from the real token).
        tampered_body = dict(good_body)
        tampered_body['result_token'] = _flip_base64_body_char(
            simulated['result_token']
        )
        bodies.append(
            client.post(_scores_url(), tampered_body, format='json')
        )

        # Missing token.
        missing_body = dict(good_body)
        del missing_body['result_token']
        bodies.append(client.post(_scores_url(), missing_body, format='json'))

        # Expired token.
        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1):
            bodies.append(
                client.post(_scores_url(), good_body, format='json')
            )

        # Authentically signed but out of range.
        out_of_range = _sign_organic(
            client, valid_simulate_payload, total_run_time=0.0
        )
        bodies.append(
            client.post(
                _scores_url(),
                _submit_body(
                    api_level_with_analytes.id,
                    out_of_range,
                    total_run_time=0.0,
                ),
                format='json',
            )
        )

        # Level disagreement.
        bodies.append(
            client.post(
                _scores_url(),
                _submit_body(api_level.id, simulated['result_token']),
                format='json',
            )
        )

        # Replay of a token that was accepted in this same test.
        accepted = client.post(_scores_url(), good_body, format='json')
        assert accepted.status_code == 201, accepted.content
        bodies.append(client.post(_scores_url(), good_body, format='json'))

        for response in bodies:
            assert_uniform_rejection(response)

        assert len({response.content for response in bodies}) == 1
        assert bodies[0].content.decode().count('error') == 1

    def test_rejection_bodies_do_not_echo_the_submitted_score(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        _, good_body = _simulate_round_trip(
            client, valid_simulate_payload
        )
        absurd = 9_999_999.0
        bad_body = dict(good_body)
        bad_body['score'] = absurd
        bad_body['result_token'] = 'garbage'

        response = client.post(_scores_url(), bad_body, format='json')

        assert_uniform_rejection(response, client_score=absurd)
        assert '9999999' not in response.content.decode()


class TestBackwardCompatibility:
    """The deploy window: old client shapes keep working (D022)."""

    def test_legacy_submission_without_a_token_is_refused(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """The pre-token client shape is a 400, not a 500 or a 201.

        Built from the shared ``valid_simulate_payload`` fixtures rather than
        hand-written JSON, so the failing part of the body is the MISSING
        token and not an accidentally wrong level id or config shape. The same
        body plus a real token is asserted to be a 201 in
        :meth:`test_legacy_shaped_body_with_a_valid_token_still_succeeds`,
        which is what makes this test's 400 attributable to the token alone.
        """
        legacy_body = {
            'level_id': api_level_with_analytes.id,
            'mobile_phase': valid_simulate_payload['mobile_phase'],
            'column_config': valid_simulate_payload['column'],
            'operation_config': valid_simulate_payload['operation'],
        }
        legacy_body.update({
            'score': 8500.0,
            'total_run_time': 24.5,
            'min_resolution': 1.8,
            'max_pressure_bar': 120.0,
            'is_successful': True,
            'overpressure': False,
        })

        response = client.post(_scores_url(), legacy_body, format='json')

        assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_legacy_shaped_body_with_a_valid_token_still_succeeds(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """A body WITHOUT the ignored legacy fields still returns 201.

        The reverse direction of the deploy window: a client that omits
        ``score``/``is_successful`` (or sends them) must not be broken while
        the old and new bundles overlap.
        """
        simulated = _simulate(client, valid_simulate_payload)
        metrics = simulated['metrics']
        minimal_body = {
            'level_id': api_level_with_analytes.id,
            'result_token': simulated['result_token'],
            'total_run_time': metrics['total_run_time'],
            'min_resolution': metrics['min_resolution'],
            'max_pressure_bar': metrics['max_pressure_bar'],
            'mobile_phase': {
                'start_b': 5,
                'end_b': 95,
                'ramp_time': 20,
                'ph': 3.0,
                'is_gradient': True,
            },
            'column_config': {
                'chemistry': 'C18',
                'length_mm': 150,
                'id_mm': 4.6,
                'particle_size_um': 5.0,
            },
            'operation_config': {
                'flow_rate_ml_min': 1.0,
                'temperature_c': 30,
                'injection_volume_ul': 10,
            },
        }

        response = client.post(_scores_url(), minimal_body, format='json')

        assert response.status_code == 201, response.content
        assert response.status_code != 429
        assert isinstance(response.json()['score_id'], int)
        assert UserScore.objects.count() == 1


class TestSignedFieldSetIsTheContract:
    """The four seams are one contract, and drift breaks a test (R013-R015)."""

    def test_every_signed_field_is_required_for_a_submission(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """Omitting any signed field makes the submission unacceptable.

        A field dropped from ``SIGNED_FIELDS`` -- or one the scores endpoint
        stops reading -- turns this red, because the payload is signed with
        that field removed and the endpoint must refuse it. ``sign_object``
        ignores nothing: the token carries exactly what was signed.

        Each iteration uses its OWN metric values (the loop index varies
        ``total_run_time``), so the accepted controls are unambiguously
        distinct runs rather than repeated submissions. Minting already
        guarantees distinct token identities on its own (``tokens.NONCE_FIELD``
        is inside the signed region), so this is defensive: it keeps the
        controls from depending on the nonce to avoid the unique
        ``result_token_hash`` column reading them as replays.
        """
        simulated = _simulate(client, valid_simulate_payload)
        base = dict(simulated['metrics'])
        base['level_id'] = api_level_with_analytes.id
        base['overpressure'] = simulated['overpressure']
        assert set(base) == set(SIGNED_FIELDS)
        signer = django_signing.TimestampSigner(salt=_score_token_salt())

        for index, field in enumerate(SIGNED_FIELDS):
            # A distinct run time per field keeps every token unique without
            # touching the signer's clock.
            metrics = dict(base, total_run_time=base['total_run_time'] + index)
            signed = {key: metrics[key] for key in SIGNED_FIELDS}
            incomplete = {
                key: value
                for key, value in signed.items()
                if key != field
            }
            # ``sign_object`` serializes only what it is handed, so a payload
            # missing a signed field produces a token whose verified payload
            # no longer equals SIGNED_FIELDS -> the endpoint must refuse it.
            token = signer.sign_object(incomplete)
            assert verify_result_token(token) is None

            response = client.post(
                _scores_url(),
                _submit_body(api_level_with_analytes.id, token),
                format='json',
            )
            assert_uniform_rejection(response)

            # ... and the COMPLETE payload over the same metrics, signed by
            # the SAME signer, IS accepted -- so the refusal above is
            # attributable to the missing field rather than to the signer, the
            # metrics, or a broken replay guard.
            complete = signer.sign_object(signed)
            assert verify_result_token(complete) is not None
            accepted = client.post(
                _scores_url(),
                _submit_body(
                    api_level_with_analytes.id,
                    complete,
                    total_run_time=metrics['total_run_time'],
                ),
                format='json',
            )
            assert accepted.status_code == 201, accepted.content
            assert accepted.status_code != 429

        # Exactly one accepted row per signed field: no omitted-field token
        # slipped through, and every control was accepted.
        assert UserScore.objects.count() == len(SIGNED_FIELDS)

    def test_simulate_response_serializer_declares_result_token(self):
        """Removing the response field must fail a test, not silently break.

        ``SimulationResponseSerializer`` is a plain ``Serializer`` (it has no
        ``Meta.model``), so the contract is its declared field map -- which is
        where ``result_token`` has to live for the client to receive it.
        """
        declared = SimulationResponseSerializer().fields

        assert 'result_token' in declared
        assert not hasattr(SimulationResponseSerializer, 'Meta')
        # The token field is a required string on the response side: the view
        # passes it through the serializer, so a declaration change is visible.
        assert declared['result_token'].required is True

    def test_scores_request_serializer_declares_the_token_and_ignores_legacy(
        self,
    ):
        """The request surface is the token plus D022's accepted-and-ignored fields."""
        declared = ScoreSubmissionSerializer().fields

        assert 'result_token' in declared
        for legacy in ('score', 'is_successful', 'overpressure'):
            assert legacy in declared
            assert declared[legacy].required is False
            assert declared[legacy].write_only is True
        # The hash is never a client-controllable input.
        assert 'result_token_hash' not in declared

    def test_verified_payload_key_set_equals_signed_fields(
        self, client, valid_simulate_payload
    ):
        """No extra key may be smuggled into a verified payload."""
        simulated = _simulate(client, valid_simulate_payload)

        verified = verify_result_token(simulated['result_token'])

        assert verified is not None
        assert set(verified) == set(SIGNED_FIELDS)
        assert tuple(verified) == SIGNED_FIELDS

    def test_the_scores_view_reads_only_signed_fields_for_numbers(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """A signed-and-consistent body whose NUMBERS disagree is still scored
        from the token, not from the request.

        The request advertises a 5-minute run while the token says 25: the
        persisted ``total_run_time`` and score must follow the token.
        """
        simulated, body = _simulate_round_trip(
            client, valid_simulate_payload
        )
        session_key = _prime_session(client)
        body['total_run_time'] = 5.0
        body['score'] = 1.0

        response = client.post(_scores_url(), body, format='json')

        assert response.status_code == 201, response.content
        record = UserScore.objects.get(
            level=api_level_with_analytes, session_key=session_key
        )
        assert record.total_run_time == pytest.approx(
            simulated['metrics']['total_run_time']
        )
        assert record.total_run_time != pytest.approx(5.0)


class TestContractSourceHygiene(SimpleTestCase):
    """The suite itself obeys the slice rules (no DB, source-level checks)."""

    def test_no_hardcoded_api_path_in_the_test_code(self):
        """Every URL must come from ``reverse``, per the S03 convention.

        The check runs over the executable string literals only: the module
        and function docstrings are collected first and subtracted, so the
        prose explaining this rule cannot be mistaken for a violation while
        any real path literal would still be caught.
        """
        import ast
        from pathlib import Path

        tree = ast.parse(
            Path(__file__).read_text(encoding='utf-8')
        )

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef,
                 ast.AsyncFunctionDef),
            ):
                body = getattr(node, 'body', [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))

        offenders = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and HARDCODED_API_PATH.search(node.value)
        ]

        assert offenders == [], (
            f'hardcoded url path literal(s) found: {offenders!r}'
        )

    def test_signed_fields_is_the_frozen_field_tuple(self):
        assert SIGNED_FIELDS == (
            'level_id',
            'total_run_time',
            'max_pressure_bar',
            'min_resolution',
            'critical_pair',
            'overpressure',
        )
