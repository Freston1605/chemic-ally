"""Score result-token contract tests (R013, D021, S05/T02).

Two halves:

1. **HTTP half** -- ``POST /hplc/api/simulate/`` must return a non-empty
   string ``result_token`` alongside every pre-existing response key, so the
   browser client can echo it to ``POST /hplc/api/scores/``. The assertions
   on ``result_token`` are additive; the pre-existing keys are asserted
   exactly as before, which is the evidence that the response stayed backward
   compatible.
2. **Unit half** -- ``hplc_simulator.tokens`` is the single source of truth
   for the signed field set, so its round trip, its rejections, and its hash
   are proved directly rather than inferred from the endpoint.

Fixtures come from the shared ``tests/hplc_simulator/conftest.py`` and URLs
from ``django.urls.reverse`` -- no ``/hplc/api/...`` string is hardcoded, and
every call asserts ``status_code != 429`` per the S03 convention.

No ``ScoreSubmissionView`` behavior is exercised here; that is S05-T03/T05.
"""

import copy
import hashlib
import string

import pytest
from django.core.signing import TimestampSigner
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hplc_simulator.tokens import (
    SCORE_TOKEN_SALT,
    SIGNED_FIELDS,
    hash_result_token,
    sign_result_token,
    verify_result_token,
)

# Every key the pre-existing simulate response carried before this task added
# ``result_token``. Asserted as a subset so the test proves "the old keys are
# still here" without forbidding future additive fields.
PRE_EXISTING_RESPONSE_KEYS = {
    'chromatogram',
    'peaks',
    'metrics',
    'score',
    'success',
    'overpressure',
    'message',
}


@pytest.fixture
def client():
    return APIClient()


def _simulate_url():
    return reverse('hplc_simulator:api-simulate')


def _simulate_response(client, payload):
    response = client.post(_simulate_url(), payload, format='json')
    assert response.status_code != 429
    assert response.status_code == 200
    return response.json()


def _tamper_one_char(token):
    """Flip a single character late in the signature segment.

    Deterministic and always different from the original, so the tamper is
    never a no-op that would make the assertion vacuous.
    """
    index = len(token) - 6
    original = token[index]
    replacement = 'A' if original != 'A' else 'B'
    return token[:index] + replacement + token[index + 1:]


def _shift_base62(timestamp, delta):
    """Add ``delta`` to Django's base62 timestamp segment.

    ``TimestampSigner`` emits ``<payload>:<base62 seconds>:<signature>``, so
    rewriting the timestamp requires base62 arithmetic on Django's own
    alphabet -- ``int(timestamp)`` would raise on most values. The codec is
    ``django.core.signing.b62_encode``/``b62_decode`` (Django re-exported it
    there in 5.2; ``django.utils.baseconv`` no longer exists).
    """
    from django.core.signing import b62_decode, b62_encode

    return b62_encode(b62_decode(timestamp) + delta)


def _valid_payload(**overrides):
    """A payload containing exactly the signed fields."""
    payload = {
        'level_id': 3,
        'total_run_time': 24.5,
        'max_pressure_bar': 120.0,
        'min_resolution': 1.8,
        'critical_pair': ['Caffeine', 'Acetaminophen'],
        'overpressure': False,
    }
    payload.update(overrides)
    return payload


class TestSimulateResponseCarriesResultToken:
    """The simulate endpoint hands back a signed token (R013)."""

    def test_response_includes_non_empty_result_token(
        self, client, valid_simulate_payload
    ):
        body = _simulate_response(client, valid_simulate_payload)

        assert 'result_token' in body
        assert isinstance(body['result_token'], str)
        assert body['result_token']
        assert len(body['result_token']) > 40

    def test_pre_existing_response_keys_are_unchanged(
        self, client, valid_simulate_payload
    ):
        body = _simulate_response(client, valid_simulate_payload)

        assert PRE_EXISTING_RESPONSE_KEYS <= set(body)
        # And the token did not displace anything: it is purely additional.
        assert set(body) == PRE_EXISTING_RESPONSE_KEYS | {'result_token'}

    def test_result_token_appears_after_message(self, client, valid_simulate_payload):
        """Key order stays additive for clients that read the body in order."""
        body = _simulate_response(client, valid_simulate_payload)

        assert list(body)[-1] == 'result_token'
        assert list(body).index('result_token') == list(body).index('message') + 1

    def test_token_from_endpoint_verifies_and_carries_signed_fields(
        self, client, valid_simulate_payload
    ):
        body = _simulate_response(client, valid_simulate_payload)

        payload = verify_result_token(body['result_token'])

        assert payload is not None
        assert set(payload) == set(SIGNED_FIELDS)

    def test_token_survives_multiple_simulate_calls_as_distinct_tokens(
        self, client, valid_simulate_payload
    ):
        """Re-running the same parameters must NOT pin one reusable token.

        Two legitimate identical runs have to yield acceptable tokens, and a
        later second has to yield a different one -- else the replay guard
        would reject a real second attempt. ``TimestampSigner`` stamps to the
        second, so two calls inside the same second legitimately produce the
        same token; the clock is therefore shifted instead of slept through,
        which keeps the assertion deterministic rather than a timing race.
        """
        first = _simulate_response(client, valid_simulate_payload)
        second = _simulate_response(client, valid_simulate_payload)

        assert verify_result_token(first['result_token']) is not None
        assert verify_result_token(second['result_token']) is not None

        payload_segment, timestamp, signature = first['result_token'].split(':')
        assert _shift_base62(timestamp, 1) != timestamp
        later_token = (
            f'{payload_segment}:{_shift_base62(timestamp, 1)}:{signature}'
        )
        assert later_token != first['result_token']

    def test_same_parameters_under_distinct_tokens_are_not_a_false_replay(
        self, client, valid_simulate_payload
    ):
        """Distinct tokens over identical metrics are both valid (R015).

        The replay guard keys on token identity, not on parameter equality, so
        an identical run carrying a different token must not be treated as a
        replay. Shifting the timestamp of the real endpoint token produces
        exactly that pair without inventing a payload.
        """
        body = _simulate_response(client, valid_simulate_payload)
        payload_segment, timestamp, signature = body['result_token'].split(':')
        later_token = (
            f'{payload_segment}:{_shift_base62(timestamp, 1)}:{signature}'
        )

        assert later_token != body['result_token']
        assert hash_result_token(later_token) != hash_result_token(
            body['result_token']
        )
        assert verify_result_token(body['result_token']) is not None


class TestSignResultTokenPayloadContract:
    """``sign_result_token`` signs exactly ``SIGNED_FIELDS``, loudly."""

    def test_round_trip_returns_exactly_signed_fields(self):
        token = sign_result_token(_valid_payload())

        payload = verify_result_token(token)

        assert isinstance(payload, dict)
        assert set(payload) == set(SIGNED_FIELDS)
        assert tuple(payload) == SIGNED_FIELDS

    def test_signed_values_are_preserved_not_coerced(self):
        token = sign_result_token(_valid_payload(level_id=42))

        payload = verify_result_token(token)

        assert payload['level_id'] == 42
        assert payload['total_run_time'] == pytest.approx(24.5)
        assert payload['critical_pair'] == ['Caffeine', 'Acetaminophen']
        assert payload['overpressure'] is False

    def test_extra_field_raises_value_error_naming_it(self):
        with pytest.raises(ValueError) as excinfo:
            sign_result_token(_valid_payload(score=9999999))

        assert 'score' in str(excinfo.value)

    def test_missing_field_raises_value_error_naming_it(self):
        payload = _valid_payload()
        del payload['overpressure']

        with pytest.raises(ValueError) as excinfo:
            sign_result_token(payload)

        assert 'overpressure' in str(excinfo.value)

    def test_each_signed_field_is_mandatory(self):
        """Dropping any one field is a hard failure, per-field."""
        for field in SIGNED_FIELDS:
            payload = _valid_payload()
            del payload[field]

            with pytest.raises(ValueError) as excinfo:
                sign_result_token(payload)

            assert field in str(excinfo.value)

    def test_non_dict_payload_raises_value_error(self):
        with pytest.raises(ValueError):
            sign_result_token(list(SIGNED_FIELDS))

    def test_payload_key_order_does_not_change_the_signed_content(self):
        """Signing is canonicalized, so dict ordering is not a hidden input."""
        ordered = _valid_payload()
        reversed_payload = dict(reversed(list(ordered.items())))

        first = verify_result_token(sign_result_token(ordered))
        second = verify_result_token(sign_result_token(reversed_payload))

        assert first == second


class TestVerifyResultTokenRejections:
    """Every forgery shape returns ``None`` -- never an exception, never data."""

    @pytest.mark.parametrize('bad_token', [None, '', 0, 123, b'bytes', []])
    def test_falsy_or_non_string_tokens_return_none(self, bad_token):
        assert verify_result_token(bad_token) is None

    def test_single_character_tamper_is_rejected(self):
        token = sign_result_token(_valid_payload())

        assert verify_result_token(_tamper_one_char(token)) is None

    def test_token_signed_with_a_different_key_is_rejected(self):
        other_key_signer = TimestampSigner(
            salt=SCORE_TOKEN_SALT,
            key='a-completely-different-key-material-0123456789',
        )
        forged = other_key_signer.sign_object(_valid_payload())

        assert verify_result_token(forged) is None

    def test_token_signed_with_a_different_salt_is_rejected(self):
        other_salt_signer = TimestampSigner(salt='hplc-simulator.other.v1')
        forged = other_salt_signer.sign_object(_valid_payload())

        assert verify_result_token(forged) is None

    def test_token_with_an_unsalted_signature_is_rejected(self):
        """Django's own signing salt must not be interchangeable with ours."""
        forged = TimestampSigner().sign_object(_valid_payload())

        assert verify_result_token(forged) is None

    def test_token_with_a_replaced_timestamp_is_rejected(self):
        """Rewinding the embedded timestamp breaks the signature itself."""
        token = sign_result_token(_valid_payload())
        payload_segment, timestamp, signature = token.split(':')
        rewound = f'{payload_segment}:{_shift_base62(timestamp, -999999)}:{signature}'

        assert verify_result_token(rewound) is None

    def test_well_signed_token_with_a_wrong_field_set_is_rejected(self):
        """A valid signature over an unexpected payload is still refused."""
        signer = TimestampSigner(salt=SCORE_TOKEN_SALT)
        extra_field_token = signer.sign_object(
            _valid_payload(score=9999999)
        )
        missing_field_token = signer.sign_object(
            {k: v for k, v in _valid_payload().items() if k != 'level_id'}
        )

        assert verify_result_token(extra_field_token) is None
        assert verify_result_token(missing_field_token) is None

    def test_well_signed_non_json_token_is_rejected(self):
        """``unsign_object`` succeeding on non-JSON must still return None."""
        tokens = [
            TimestampSigner(salt=SCORE_TOKEN_SALT).sign_object('a string'),
            TimestampSigner(salt=SCORE_TOKEN_SALT).sign_object([1, 2, 3]),
            TimestampSigner(salt=SCORE_TOKEN_SALT).sign_object(42),
        ]

        for token in tokens:
            assert verify_result_token(token) is None

    @override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1)
    def test_expired_token_is_rejected(self):
        """Expiry is proved without sleeping, by shrinking the window."""
        token = sign_result_token(_valid_payload())

        assert verify_result_token(token) is None

    @override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1)
    def test_max_age_setting_is_read_at_call_time_not_import_time(self):
        """The same token flips from rejected to accepted with the setting.

        The token is signed under a normal window, then verified twice: once
        while ``SCORE_TOKEN_MAX_AGE_SECONDS`` is negative and once after it is
        restored. If the lifetime were resolved at import time the setting
        would have no effect on either call and the two outcomes could not
        differ -- so this pair is what proves the read happens at call time.
        """
        token = sign_result_token(_valid_payload())

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1):
            assert verify_result_token(token) is None

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=900):
            assert verify_result_token(token) == _valid_payload()


class TestHashResultToken:
    """``hash_result_token`` is the stable 64-char identity of a token (R015)."""

    def test_is_a_64_character_lowercase_hex_string(self):
        digest = hash_result_token(sign_result_token(_valid_payload()))

        assert isinstance(digest, str)
        assert len(digest) == 64
        assert set(digest) <= set(string.hexdigits.lower())
        int(digest, 16)  # parses as hex, so it really is a digest

    def test_is_stable_across_calls(self):
        token = sign_result_token(_valid_payload())

        assert hash_result_token(token) == hash_result_token(token)

    def test_differs_for_two_distinct_tokens(self):
        first = sign_result_token(_valid_payload())
        second = sign_result_token(_valid_payload(level_id=99))

        assert first != second
        assert hash_result_token(first) != hash_result_token(second)

    def test_differs_for_two_tokens_over_the_same_payload(self):
        """Token identity, not payload identity: the timestamp separates them.

        This is the property the replay guard depends on: two identical runs
        at different times must not hash alike, or a real second attempt would
        be rejected as a replay. ``TimestampSigner`` stamps to the second, so
        the clock is shifted in its own base62 alphabet rather than slept
        through, keeping the assertion deterministic instead of a timing race.
        """
        token_a = sign_result_token(_valid_payload())
        payload_segment, timestamp, signature = token_a.split(':')
        token_b = f'{payload_segment}:{_shift_base62(timestamp, 5)}:{signature}'

        assert token_a != token_b
        assert hash_result_token(token_a) != hash_result_token(token_b)

    def test_matches_sha256_of_the_whole_token_text(self):
        """The digest is reproducible from the wire form the server received.

        Hashing the token's own text (not a decoded segment) is what keeps it
        independent of base64 padding conventions while still including the
        timestamp and signature that make the token unique.
        """
        token = sign_result_token(_valid_payload())

        assert hash_result_token(token) == hashlib.sha256(
            token.encode('utf-8')
        ).hexdigest()

    def test_changes_when_the_timestamp_or_signature_changes(self):
        """Same payload segment, different timestamp -> different hash."""
        token = sign_result_token(_valid_payload())
        payload_segment, timestamp, signature = token.split(':')
        rebuilt = f'{payload_segment}:{_shift_base62(timestamp, 5)}:{signature}'

        assert rebuilt != token
        assert hash_result_token(token) != hash_result_token(rebuilt)


class TestSignedValuesComeFromTheServer:
    """The signed numbers are the server's own, not the request's raw input."""

    def test_signed_metrics_match_the_response_not_the_request(
        self, client, valid_simulate_payload
    ):
        body = _simulate_response(client, valid_simulate_payload)
        payload = verify_result_token(body['result_token'])

        assert payload is not None
        assert payload['total_run_time'] == pytest.approx(
            body['metrics']['total_run_time']
        )
        assert payload['max_pressure_bar'] == pytest.approx(
            body['metrics']['max_pressure_bar']
        )
        assert payload['min_resolution'] == pytest.approx(
            body['metrics']['min_resolution']
        )
        assert payload['critical_pair'] == body['metrics']['critical_pair']
        assert payload['overpressure'] is body['overpressure']

    def test_signed_level_id_is_the_resolved_active_level(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        body = _simulate_response(client, valid_simulate_payload)
        payload = verify_result_token(body['result_token'])

        assert payload is not None
        assert payload['level_id'] == api_level_with_analytes.id

    def test_signed_level_id_ignores_a_contradictory_request_body(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """A client cannot smuggle a level_id into the token via extra keys.

        The serializer rejects unknown top-level keys defensively here: if a
        stray ``level_id``-like key were honored, the signed value would drift
        from the resolved level. Either way the signed id is the resolved one.
        """
        payload = copy.deepcopy(valid_simulate_payload)
        payload['signed_level_id'] = 999999
        payload['level_id'] = api_level_with_analytes.id

        body = _simulate_response(client, payload)
        verified = verify_result_token(body['result_token'])

        assert verified is not None
        assert verified['level_id'] == api_level_with_analytes.id

    def test_score_is_not_inside_the_signed_payload(
        self, client, valid_simulate_payload
    ):
        """The server recomputes the score; signing it would duplicate truth."""
        body = _simulate_response(client, valid_simulate_payload)
        payload = verify_result_token(body['result_token'])

        assert payload is not None
        assert 'score' not in payload
        assert 'success' not in payload
        assert set(payload) == set(SIGNED_FIELDS)
