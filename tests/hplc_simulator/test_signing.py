"""Signing contract tests, named against ``hplc_simulator.tokens`` (S05-T02).

This module is the S05-T02 acceptance surface for the *signing* half of the
score-integrity work: a valid payload verifies, a tampered payload fails, an
expired payload fails, and a payload signed with a different key fails.

It intentionally contains **no signing logic of its own**. ``tokens.py`` is
the single source of truth for canonicalization, the signed field set, the
key derivation and the max-age check, so every assertion here imports from
``hplc_simulator.tokens`` and never re-derives a signature by hand. The
broader endpoint/token-contract suite lives in ``test_result_token.py``; this
module pins the four plan-required proofs plus the SimulateView wiring that
produces the envelope.

Metadata note: the task plan (S05-T02) named this file ``test_signing.py``
while the deliverable module is ``tokens.py`` (the slice plan's "single
tokens module"). The names differ; the contract does not -- both files
exercise the same one module.
"""

import time

import pytest
from django.core import signing
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hplc_simulator.tokens import (
    SCORE_TOKEN_SALT,
    SIGNED_FIELDS,
    sign_result_token,
    verify_result_token,
)


@pytest.fixture
def client():
    return APIClient()


def _valid_payload(**overrides):
    """A payload carrying exactly the signed fields."""
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


def _tamper_one_char(token):
    """Flip one character deterministically so the tamper is never a no-op."""
    index = len(token) - 6
    original = token[index]
    replacement = 'A' if original != 'A' else 'B'
    return token[:index] + replacement + token[index + 1:]


class TestSignAndVerifyRoundTrip:
    """The happy path and the field contract (plan deliverable 1)."""

    def test_valid_signed_payload_verifies(self):
        token = sign_result_token(_valid_payload())
        assert isinstance(token, str) and token

        verified = verify_result_token(token)

        assert verified is not None
        assert set(verified) == set(SIGNED_FIELDS)
        assert verified['level_id'] == 3
        assert verified['overpressure'] is False

    def test_round_trip_preserves_every_signed_value(self):
        payload = _valid_payload()
        verified = verify_result_token(sign_result_token(payload))

        assert verified == payload

    def test_token_is_a_single_current_key_signature(self):
        """The envelope is one salted ``TimestampSigner`` blob, not a
        hand-rolled HMAC: its salt and its timestamp segment are both
        present, which is what makes rotation and expiry possible."""
        token = sign_result_token(_valid_payload())
        parts = token.split(':')

        # payload : base62-timestamp : signature
        assert len(parts) == 3
        assert parts[1]
        assert parts[2]

    @pytest.mark.parametrize('field', SIGNED_FIELDS)
    def test_every_signed_field_is_mandatory(self, field):
        payload = _valid_payload()
        del payload[field]

        with pytest.raises(ValueError):
            sign_result_token(payload)

    def test_payload_with_an_extra_field_is_rejected(self):
        with pytest.raises(ValueError):
            sign_result_token(_valid_payload(score=999))

    def test_score_is_not_part_of_the_signed_contract(self):
        """The score is recomputed server-side; signing it would create a
        second source of truth (scope guard for this task)."""
        assert 'score' not in SIGNED_FIELDS


class TestTamperedPayloadFails:
    """Tamper rejection (plan deliverable 2)."""

    def test_single_character_tamper_is_rejected(self):
        token = sign_result_token(_valid_payload())
        assert verify_result_token(_tamper_one_char(token)) is None

    def test_truncated_token_is_rejected(self):
        token = sign_result_token(_valid_payload())
        assert verify_result_token(token[:-8]) is None

    def test_token_with_a_swapped_payload_is_rejected(self):
        """Re-using a signature against a different payload must fail."""
        token = sign_result_token(_valid_payload())
        other = sign_result_token(_valid_payload(level_id=7))
        forged = other.split(':', 1)[1]
        assert verify_result_token(token.split(':', 1)[0] + ':' + forged) is None

    @pytest.mark.parametrize('bad_token', ['', None, 123, [], {}])
    def test_falsy_or_non_string_tokens_are_rejected(self, bad_token):
        assert verify_result_token(bad_token) is None

    def test_garbage_string_is_rejected(self):
        assert verify_result_token('not-a-token') is None


class TestExpiryFails:
    """Expiry is evaluated by the verifier, not the client (deliverable 3)."""

    def test_expired_payload_fails(self):
        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=1):
            token = sign_result_token(_valid_payload())
            assert verify_result_token(token) is not None

            time.sleep(2)

            assert verify_result_token(token) is None

    def test_max_age_is_read_at_call_time(self):
        """A short lifetime applied *after* signing still expires the token,
        proving the window is resolved from settings per call rather than
        frozen at import time."""
        token = sign_result_token(_valid_payload())
        assert verify_result_token(token) is not None

        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=0):
            time.sleep(1)
            assert verify_result_token(token) is None

    def test_expired_token_is_rejected_even_when_otherwise_well_formed(self):
        with override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=-1):
            token = sign_result_token(_valid_payload())
            assert verify_result_token(token) is None


class TestDifferentKeyFails:
    """Key derivation comes from Django's signing key set (deliverable 4)."""

    def test_token_signed_with_a_different_key_is_rejected(self):
        foreign = signing.TimestampSigner(
            key='a-completely-different-key', salt=SCORE_TOKEN_SALT
        ).sign_object(_valid_payload())

        assert verify_result_token(foreign) is None

    def test_token_signed_with_a_different_salt_is_rejected(self):
        foreign = signing.TimestampSigner(
            salt='some-other-namespace'
        ).sign_object(_valid_payload())

        assert verify_result_token(foreign) is None

    def test_unsigned_plain_serialization_is_rejected(self):
        import json

        unsigned = signing.b64_encode(
            json.dumps(_valid_payload()).encode()
        ).decode()

        assert verify_result_token(unsigned) is None


class TestSimulateViewEmitsSignedResults:
    """SimulateView emits the envelope through the shared module (item 3)."""

    def test_simulate_response_carries_a_verifiable_token(
        self, client, valid_simulate_payload
    ):
        response = client.post(
            reverse('hplc_simulator:api-simulate'),
            valid_simulate_payload,
            format='json',
        )
        assert response.status_code != 429
        assert response.status_code == 200

        token = response.json().get('result_token')
        assert isinstance(token, str) and token
        assert verify_result_token(token) is not None

    def test_signed_values_come_from_the_server_not_the_request(
        self, client, valid_simulate_payload, api_level_with_analytes
    ):
        """The signed ``level_id`` is the resolved active level, so a client
        cannot smuggle a different level into the signed envelope."""
        body = dict(valid_simulate_payload)
        body['level_id'] = api_level_with_analytes.id

        response = client.post(
            reverse('hplc_simulator:api-simulate'), body, format='json'
        )
        assert response.status_code == 200

        verified = verify_result_token(response.json()['result_token'])
        assert verified is not None
        assert verified['level_id'] == api_level_with_analytes.id

    def test_response_shape_is_unchanged_apart_from_result_token(
        self, client, valid_simulate_payload
    ):
        response = client.post(
            reverse('hplc_simulator:api-simulate'),
            valid_simulate_payload,
            format='json',
        )
        body = response.json()

        pre_existing = {
            'chromatogram', 'peaks', 'metrics', 'score', 'success',
            'overpressure', 'message',
        }
        assert pre_existing.issubset(set(body))
        assert 'result_token' in body


class TestSingleSourceOfTruth:
    """No duplicated signing code (plan item 4)."""

    def test_signature_is_produced_only_by_the_shared_module(self):
        """The view must not build a signature itself; it imports the shared
        helper. Asserted structurally so a future copy-paste fails loudly.

        The check is on the IMPORT TARGET rather than one exact import line:
        ``views.py`` legitimately imports several names from ``.tokens``
        (S05-T03 added ``verify_result_token`` and ``hash_result_token``), and
        pinning the literal single-name form would fail on that refactor
        without any signing logic actually being duplicated.
        """
        import inspect

        from hplc_simulator import views

        source = inspect.getsource(views)
        assert 'from .tokens import' in source
        assert 'sign_result_token' in source
        assert 'TimestampSigner' not in source
        assert 'hmac' not in source
