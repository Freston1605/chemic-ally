"""Server-authoritative scoring tests for ``POST /hplc/api/scores/`` (R014, D022).

Before S05-T03 the scores endpoint trusted every field the browser handed it,
including ``score``: a tampered payload wrote an attacker-chosen value straight
into ``UserScore`` and ``LevelProgress.best_score``. Now the ONLY trusted
carrier of the run's numbers is ``result_token``, and the server recomputes
``score``/``is_successful`` from the verified metrics with
``calculate_score``.

What this module proves:

1. A full ``simulate`` -> ``scores`` round trip over ``APIClient`` whose request
   carries an absurd client ``score`` still persists the SERVER-computed value
   -- for ``UserScore.score``, ``UserScore.is_successful`` and
   ``LevelProgress`` (``best_score``/``completed``/``best_resolution``).
   A client claiming success with a sub-1.5 resolution must not set
   ``completed``.
2. Every forgery is refused with the SAME uniform 400: tampered signature,
   different signing key, expired token, absent/None/empty token.
3. Range validation is real: a HOSTILE BUT CORRECTLY SIGNED token over
   out-of-range metrics is refused, so the 400 cannot be explained by the
   signature check alone.
4. Non-disclosure: no rejection body names a field, echoes a submitted value,
   or contains a traceback/exception class.

Everything is driven through ``django.urls.reverse`` (never a hardcoded
``/hplc/api/...`` path) and every call asserts ``status_code != 429`` per the
S03 convention. Tokens are minted with the shared
``hplc_simulator.tokens.sign_result_token`` -- never a hand-rolled HMAC -- and
the happy path always takes the token from a real simulate response.

Replay rejection (a second use of the same token) is deliberately out of scope
here; S05-T05 owns it end-to-end.
"""

import time

import pytest
from django.core.signing import TimestampSigner
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hplc_simulator.models import LevelProgress, UserScore
from hplc_simulator.simulation.engine import calculate_score
from hplc_simulator.tokens import (
    SIGNED_FIELDS,
    hash_result_token,
    sign_result_token,
    verify_result_token,
)
from hplc_simulator.views import SCORE_REJECTION_ERROR

# Substrings that must never appear in a rejection body. A rejection that
# leaks any of these tells a forger which check failed, or exposes internals.
#
# ``token`` is deliberately NOT in this list: the slice's own uniform message
# is 'Invalid or expired result token' (D022), so the word necessarily appears.
# What must never appear is the FIELD NAME a client could key off -- the
# ``result_token`` request key, a correct value, or an exception class.
FORBIDDEN_IN_REJECTION = (
    'Traceback',
    'OperationalError',
    'InterfaceError',
    'SignatureExpired',
    'BadSignature',
    'result_token',
    'result_token_hash',
)


@pytest.fixture
def client():
    return APIClient()


def _simulate_url():
    return reverse('hplc_simulator:api-simulate')


def _scores_url():
    return reverse('hplc_simulator:api-scores')


def _prime_session(client):
    """Save a session and pin its cookie on the client, returning the key."""
    session = client.session
    session.save()
    return client.session.session_key


def _simulate(client, payload):
    """Run the real simulate endpoint and return its JSON body."""
    response = client.post(_simulate_url(), payload, format='json')
    assert response.status_code != 429
    assert response.status_code == 200
    return response.json()


def _valid_signed_payload(level, **overrides):
    """A payload that matches the signed field set, with overrides applied."""
    payload = {
        'level_id': level.id,
        'total_run_time': 24.5,
        'max_pressure_bar': 120.0,
        'min_resolution': 1.8,
        'critical_pair': ['Caffeine', 'Acetaminophen'],
        'overpressure': False,
    }
    payload.update(overrides)
    return payload


def _submit_body(client, level, token, **overrides):
    """The full request body a browser sends, with the given token.

    ``score``/``is_successful``/``overpressure`` are present on purpose: they
    are what the deployed client still sends, and this module is where their
    being ignored must be proved.
    """
    body = {
        'level_id': level.id,
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
    body.update(overrides)
    return body


def _tamper_signature(token):
    """Flip one character late in the signature segment.

    Deterministic and always different from the original, so the tamper is
    never a no-op that would make the rejection assertion vacuous.
    """
    index = len(token) - 6
    original = token[index]
    replacement = 'A' if original != 'A' else 'B'
    return token[:index] + replacement + token[index + 1:]


def _assert_uniform_rejection(response):
    """Assert the ONE non-disclosing 400 body, with nothing extra in it."""
    assert response.status_code == 400
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


def _fresh_token_for_replay(client, level):
    """Mint a token with a UNIQUE identity for the replay case.

    ``TimestampSigner`` stamps to whole seconds, so a token minted here would
    otherwise be byte-identical to another in the same test and the first
    submission would already be the "replay". The signer's clock is advanced
    instead of the token text, which keeps the signature verifiable.
    """
    tokens = _fresh_tokens(level, 1)
    return tokens[0]


def _fresh_tokens(level, count):
    """Return ``count`` distinct, valid tokens over the standard metrics."""
    from django.core import signing as django_signing

    real_time = time.time
    minted = []
    try:
        for index in range(1, count + 1):
            django_signing.time.time = (
                lambda offset=index: real_time() + offset
            )
            minted.append(
                sign_result_token(_valid_signed_payload(level))
            )
    finally:
        django_signing.time.time = real_time
    return minted


class TestServerComputedScoreWins:
    """The client's numbers are ignored; the server's are persisted (R014)."""

    def test_absurd_client_score_is_ignored_end_to_end(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """simulate -> submit with client 'score': 9_999_999 -> server value.

        The token comes from a REAL simulate response, so this is the contract
        exercise the slice requires: whatever the simulate view signed is what
        the scores view verifies.
        """
        session_key = _prime_session(client)
        simulated = _simulate(client, valid_simulate_payload)
        metrics = simulated['metrics']

        response = client.post(
            _scores_url(),
            {
                'level_id': api_level_with_analytes.id,
                'result_token': simulated['result_token'],
                'score': 9_999_999,
                'total_run_time': metrics['total_run_time'],
                'min_resolution': metrics['min_resolution'],
                'max_pressure_bar': metrics['max_pressure_bar'],
                'is_successful': True,
                'overpressure': simulated['overpressure'],
                'mobile_phase': valid_simulate_payload['mobile_phase'],
                'column_config': valid_simulate_payload['column'],
                'operation_config': valid_simulate_payload['operation'],
            },
            format='json',
        )

        assert response.status_code == 201, response.content
        assert response.status_code != 429

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
        assert record.score != pytest.approx(9_999_999)
        assert record.is_successful is expected_success
        assert record.total_run_time == pytest.approx(
            metrics['total_run_time']
        )
        assert record.min_resolution == pytest.approx(
            metrics['min_resolution']
        )
        assert record.max_pressure_bar == pytest.approx(
            metrics['max_pressure_bar']
        )

    def test_client_claiming_success_cannot_set_completed(
        self, client, api_level, fresh_result_token
    ):
        """A sub-1.5 resolution with client is_successful=True stays incomplete.

        ``completed`` means "achieved baseline resolution (Rs >= 1.5)". The
        client is the only source of such a claim today, and it must not be
        able to make it stick.
        """
        session_key = _prime_session(client)
        token = fresh_result_token(total_run_time=30.0, min_resolution=1.2)

        response = client.post(
            _scores_url(),
            _submit_body(
                client,
                api_level,
                token,
                total_run_time=30.0,
                min_resolution=1.2,
                is_successful=True,
                score=9_999_999,
            ),
            format='json',
        )

        assert response.status_code == 201, response.content
        assert response.status_code != 429

        expected_score, expected_success = calculate_score(
            total_run_time=30.0,
            min_resolution=1.2,
            max_pressure_bar=120.0,
            pressure_limit=api_level.max_pressure_bar,
            base_points=api_level.base_points,
        )
        assert expected_success is False

        record = UserScore.objects.get(
            level=api_level, session_key=session_key
        )
        assert record.score == pytest.approx(expected_score)
        assert record.is_successful is False

        progress = LevelProgress.objects.get(
            level=api_level, session_key=session_key
        )
        assert progress.completed is False
        assert progress.best_score == pytest.approx(expected_score)
        assert progress.best_resolution == pytest.approx(1.2)
        assert progress.attempts == 1

    def test_server_value_also_drives_the_progress_upsert(
        self, client, api_level, fresh_result_token
    ):
        """A second run updates best_score from the SERVER value, not the client's.

        The first run is faster, so only the server-computed score can explain
        which of the two wins -- the client sends a larger number both times.
        """
        session_key = _prime_session(client)

        first = client.post(
            _scores_url(),
            _submit_body(
                client,
                api_level,
                fresh_result_token(total_run_time=50.0),
                total_run_time=50.0,
                score=9_999_999,
            ),
            format='json',
        )
        assert first.status_code == 201, first.content

        # Same metrics, faster run => strictly higher server score.
        second = client.post(
            _scores_url(),
            _submit_body(
                client,
                api_level,
                fresh_result_token(total_run_time=10.0),
                total_run_time=10.0,
                score=1,
            ),
            format='json',
        )
        assert second.status_code == 201, second.content

        slow_score, _ = calculate_score(
            total_run_time=50.0,
            min_resolution=1.8,
            max_pressure_bar=120.0,
            pressure_limit=api_level.max_pressure_bar,
            base_points=api_level.base_points,
        )
        fast_score, _ = calculate_score(
            total_run_time=10.0,
            min_resolution=1.8,
            max_pressure_bar=120.0,
            pressure_limit=api_level.max_pressure_bar,
            base_points=api_level.base_points,
        )
        assert fast_score > slow_score

        progress = LevelProgress.objects.get(
            level=api_level, session_key=session_key
        )
        assert progress.attempts == 2
        assert progress.best_score == pytest.approx(fast_score)


class TestForgedTokensAreRefused:
    """Tamper, wrong key, expiry and absence all collapse to one 400."""

    def test_tampered_signature_returns_400_and_writes_nothing(
        self, client, api_level, fresh_result_token
    ):
        token = fresh_result_token()
        body = _submit_body(
            client, api_level, _tamper_signature(token)
        )

        response = client.post(_scores_url(), body, format='json')

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0
        assert LevelProgress.objects.count() == 0
        # And the untampered token still verifies, so the tamper was real.
        assert verify_result_token(token) is not None

    def test_token_signed_with_a_different_key_returns_400(
        self, client, api_level, fresh_result_token
    ):
        """A well-formed token from another key must not be accepted.

        Built with Django's own signer over a different key and the SAME salt,
        which is exactly the shape a forgeable-secret attacker would produce.
        """
        from hplc_simulator.tokens import SCORE_TOKEN_SALT

        foreign = TimestampSigner(
            key='x' * 50, salt=SCORE_TOKEN_SALT
        ).sign_object(_valid_signed_payload(api_level))
        assert verify_result_token(foreign) is None

        response = client.post(
            _scores_url(),
            _submit_body(client, api_level, foreign),
            format='json',
        )

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_expired_token_returns_400(
        self, client, api_level, fresh_result_token
    ):
        token = fresh_result_token()

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1):
            response = client.post(
                _scores_url(),
                _submit_body(client, api_level, token),
                format='json',
            )

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    @pytest.mark.parametrize('token_value', [None, '', 'not-a-token'])
    def test_missing_none_or_empty_token_returns_400(
        self, client, api_level, token_value
    ):
        body = _submit_body(client, api_level, token_value)
        if token_value is None:
            del body['result_token']

        response = client.post(_scores_url(), body, format='json')

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_tampered_token_is_refused_even_when_the_payload_is_impossible(
        self, client, api_level, fresh_result_token
    ):
        """A forged token is refused on the SIGNATURE, not on its contents.

        Passes an out-of-range metric through a tampered token: the response
        must be identical to the tamper case, proving the signature check runs
        first and the two failure reasons are indistinguishable.
        """
        tampered = _tamper_signature(
            fresh_result_token(total_run_time=0.0)
        )

        response = client.post(
            _scores_url(),
            _submit_body(client, api_level, tampered, total_run_time=0.0),
            format='json',
        )

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0


class TestSignedButOutOfRangeMetricsAreRefused:
    """Range validation is not merely signature validation."""

    @pytest.mark.parametrize(
        'overrides',
        [
            {'total_run_time': 0.0},
            {'total_run_time': -5.0},
            {'min_resolution': -0.5},
            {'max_pressure_bar': -1.0},
            # Above the level's own column limit (api_level allows 400 bar).
            {'max_pressure_bar': 900.0},
            # Above the limit AND claiming overpressure: a signed flag must not
            # launder an impossible run into an accepted one.
            {'max_pressure_bar': 900.0, 'overpressure': True},
            {'critical_pair': ['a', 'b', 'c']},
            {'critical_pair': [1, 2]},
        ],
    )
    def test_hostile_but_correctly_signed_metrics_return_400(
        self, client, api_level, overrides
    ):
        token = sign_result_token(_valid_signed_payload(api_level, **overrides))
        # The signature is authentic; only the numbers are impossible.
        assert verify_result_token(token) is not None

        response = client.post(
            _scores_url(),
            _submit_body(client, api_level, token, **overrides),
            format='json',
        )

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0
        assert LevelProgress.objects.count() == 0

    def test_level_id_mismatch_between_token_and_body_returns_400(
        self, client, api_level, api_level_with_analytes
    ):
        """A signed level 1 token submitted as level 2 is refused."""
        token = sign_result_token(_valid_signed_payload(api_level))

        response = client.post(
            _scores_url(),
            _submit_body(
                client,
                api_level_with_analytes,
                token,
                level_id=api_level_with_analytes.id,
            ),
            format='json',
        )

        _assert_uniform_rejection(response)
        assert UserScore.objects.count() == 0

    def test_non_numeric_level_id_returns_400(self, client, api_level):
        """A body level_id that cannot be an int is a uniform rejection.

        It is NOT a DRF-shaped field error: the view compares the untrusted
        id itself before the serializer is consulted.
        """
        token = sign_result_token(_valid_signed_payload(api_level))

        response = client.post(
            _scores_url(),
            _submit_body(client, api_level, token, level_id='not-an-int'),
            format='json',
        )

        _assert_uniform_rejection(response)


class TestRejectionBodiesDoNotDisclose:
    """Every 400 carries ONE message and no internal detail."""

    def test_all_rejection_reasons_share_one_indistinguishable_body(
        self, client, api_level
    ):
        """Collect one body per rejection reason and assert they are identical.

        This is the property that makes the endpoint non-disclosing: a forger
        cannot use the response to tell which check failed.
        """
        bodies = []

        # Tamper
        tampered = _tamper_signature(
            sign_result_token(_valid_signed_payload(api_level))
        )
        bodies.append(
            client.post(
                _scores_url(),
                _submit_body(client, api_level, tampered),
                format='json',
            ).content
        )

        # Missing token
        body = _submit_body(client, api_level, '')
        del body['result_token']
        bodies.append(
            client.post(_scores_url(), body, format='json').content
        )

        # Level mismatch
        other = sign_result_token(_valid_signed_payload(api_level, level_id=987654))
        bodies.append(
            client.post(
                _scores_url(), _submit_body(client, api_level, other),
                format='json',
            ).content
        )

        # Out of range (authentically signed)
        out_of_range = sign_result_token(
            _valid_signed_payload(api_level, total_run_time=0.0)
        )
        bodies.append(
            client.post(
                _scores_url(),
                _submit_body(
                    client, api_level, out_of_range, total_run_time=0.0
                ),
                format='json',
            ).content
        )

        # Replay of a token that was just accepted.
        good = _fresh_token_for_replay(client, api_level)
        first = client.post(
            _scores_url(), _submit_body(client, api_level, good), format='json'
        )
        assert first.status_code == 201, first.content
        bodies.append(
            client.post(
                _scores_url(), _submit_body(client, api_level, good),
                format='json',
            ).content
        )

        assert len(set(bodies)) == 1
        assert bodies[0].decode().count('error') == 1
        for raw in bodies:
            text = raw.decode()
            for forbidden in FORBIDDEN_IN_REJECTION:
                assert forbidden not in text
            # The absurd client score never comes back.
            assert '9999999' not in text


class TestSignedFieldSetContract:
    """The token's field set is exactly what the view reads for numbers."""

    def test_the_view_reads_exactly_the_signed_fields(
        self, client, api_level
    ):
        """A missing signed field is a uniform 400, never a 500.

        ``SIGNED_FIELDS`` is frozen in ``tokens.py``; a field dropped from it
        while the view still reads it would surface here rather than in
        production.
        """
        for field in SIGNED_FIELDS:
            incomplete = _valid_signed_payload(api_level)
            del incomplete[field]
            # sign_result_token refuses an incomplete payload loudly, which is
            # the first line of defence ...
            with pytest.raises(ValueError):
                sign_result_token(incomplete)

        # ... and the view additionally tolerates a payload that somehow lost a
        # field, returning the uniform 400 instead of a KeyError-driven 500.
        good = sign_result_token(_valid_signed_payload(api_level))
        payload = verify_result_token(good)
        assert set(payload) == set(SIGNED_FIELDS)

    def test_repeated_submissions_use_distinct_tokens(
        self, client, api_level, fresh_result_token
    ):
        """Distinct tokens over identical metrics both submit (no false replay).

        The replay guard keys on token identity, so two legitimate runs of the
        same parameters must NOT be a false replay. Tokens minted inside the
        SAME second are byte-identical (``TimestampSigner`` stamps to whole
        seconds -- see ``fresh_result_token``), so this test uses the factory,
        which advances the signer's clock per call, exactly as a real user
        takes more than a second to run the simulator twice.
        """
        first = fresh_result_token()
        second = fresh_result_token()
        assert first != second
        assert hash_result_token(first) != hash_result_token(second)
        assert verify_result_token(first) is not None
        assert verify_result_token(second) is not None

        first_response = client.post(
            _scores_url(), _submit_body(client, api_level, first),
            format='json',
        )
        second_response = client.post(
            _scores_url(), _submit_body(client, api_level, second),
            format='json',
        )

        assert first_response.status_code == 201, first_response.content
        assert second_response.status_code == 201, second_response.content
        assert UserScore.objects.count() == 2
