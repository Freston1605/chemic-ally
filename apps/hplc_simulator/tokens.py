"""Signed result tokens for the HPLC simulator score pipeline (R013, D021).

This module is the **single source of truth** for the score-token contract.
The token is the ONLY trusted carrier of simulation numbers into
``POST /hplc/api/scores/``: a simulation result exists nowhere but the
browser, so ``SimulateView`` hands the client a server-signed summary of the
run the server itself computed, and ``ScoreSubmissionView`` recomputes the
score from those verified numbers instead of trusting anything the client
sends.

Four rules follow from that:

1. **Always sign and verify with Django's signing API**
   (:class:`django.core.signing.TimestampSigner`), never with a hand-rolled
   HMAC. The built-in API is what makes ``SECRET_KEY_FALLBACKS`` key rotation
   keep working: verification transparently accepts a signature made with a
   previous key while fresh tokens are always minted with the current one. A
   custom HMAC over ``settings.SECRET_KEY`` would silently reject every token
   the moment the key is rotated.
2. **The signed field set is frozen here** (:data:`SIGNED_FIELDS`). The
   simulate view, the response serializer, the scores view, and
   :func:`hash_result_token` all derive from this one tuple, so a silent
   field-set drift becomes a test failure instead of an implicit payload
   change.
3. **The score is never signed.** The server recomputes it from the signed
   metrics, so signing it would create a second source of truth.
4. **Every mint is unique** (:data:`NONCE_FIELD`). ``TimestampSigner`` stamps
   to whole seconds and nothing else in a token varies between two runs of
   the *same* simulation, so without a nonce two identical runs inside one
   wall-clock second would serialize byte-identically -- and the unique
   ``UserScore.result_token_hash`` column would then refuse the second
   legitimate submission as a replay. A fresh random nonce inside the signed
   region makes token identity one-per-mint, which is exactly what the replay
   guard means by identity.

The token also carries the signer's timestamp, which is why verification can
enforce :data:`MAX_AGE_SECONDS` and reject a replayed, long-lived token even
though the same run submitted twice under two distinct tokens is legitimate.
"""

import hashlib
import secrets

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

# The exact, ordered set of simulation numbers the token vouches for. One
# tuple, consumed by the signing and verifying paths and asserted against by
# the test suite (S05-T02/T05) so drift is loud.
SIGNED_FIELDS = (
    'level_id',
    'total_run_time',
    'max_pressure_bar',
    'min_resolution',
    'critical_pair',
    'overpressure',
)

# An explicit salt keeps this token out of the namespace of Django's other
# signed values (session cookies, password-reset tokens, messages ...). A
# signature minted for one purpose must never be accepted for another.
SCORE_TOKEN_SALT = 'hplc-simulator.score-token.v1'

# Fallback when ``SCORE_TOKEN_MAX_AGE_SECONDS`` is not configured. 15 minutes
# is long enough to finish a run and submit it, short enough that a leaked
# token is not reusable later.
DEFAULT_MAX_AGE_SECONDS = 900

# Key that :func:`sign_result_token` mixes into the signed region so that no
# two mints are ever byte-identical. It is INSIDE the signature (tampering
# with it invalidates the token) and it is stripped again by
# :func:`verify_result_token`, so the verified payload is still exactly
# :data:`SIGNED_FIELDS` -- see the module docstring, rule 4. The leading
# underscore keeps it out of the namespace of real simulation fields.
NONCE_FIELD = '_nonce'

# Entropy per mint: 16 bytes = 32 lowercase hex characters. Token uniqueness
# only needs a collision-free identity among the handful of runs one player
# submits inside a single token lifetime, so this is far past sufficient.
NONCE_BYTES = 16


def _max_age_seconds():
    """Resolve the token lifetime, read at call time.

    Reading the setting inside the function (instead of at import time) is
    what lets tests use ``override_settings(SCORE_TOKEN_MAX_AGE_SECONDS=...)``
    and lets production tune the window without touching this module.
    """
    return getattr(
        settings, 'SCORE_TOKEN_MAX_AGE_SECONDS', DEFAULT_MAX_AGE_SECONDS
    )


def _signer():
    return TimestampSigner(salt=SCORE_TOKEN_SALT)


def sign_result_token(payload):
    """Return a signed token for exactly :data:`SIGNED_FIELDS` of ``payload``.

    ``payload`` must carry every signed field and nothing else: a missing or
    extra key raises :class:`ValueError` naming the offending keys rather than
    being silently dropped, because silently dropping a field would change
    what the scores endpoint can verify without any visible signal.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            'result token payload must be a dict, got '
            f'{type(payload).__name__}'
        )

    actual = set(payload)
    expected = set(SIGNED_FIELDS)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        problems = []
        if missing:
            problems.append(f'missing keys {missing}')
        if extra:
            problems.append(f'unexpected keys {extra}')
        raise ValueError(
            'result token payload must contain exactly the signed fields: '
            + '; '.join(problems)
        )

    # Rebuild in SIGNED_FIELDS order so the serialized payload is canonical
    # regardless of the caller's dict ordering.
    canonical = {field: payload[field] for field in SIGNED_FIELDS}
    # Rule 4: one fresh nonce per mint, so two runs of the same simulation in
    # the same second still produce two distinct tokens. Added AFTER the
    # exact-field-set validation above, so callers still pass (and are still
    # checked for) exactly SIGNED_FIELDS.
    canonical[NONCE_FIELD] = secrets.token_hex(NONCE_BYTES)
    return _signer().sign_object(canonical)


def verify_result_token(token):
    """Return the verified payload for ``token``, or ``None`` if it is invalid.

    ``None`` is returned for a falsy or non-string token, for an expired token
    (:class:`~django.core.signing.SignatureExpired`) and for a tampered or
    differently-keyed token (:class:`~django.core.signing.BadSignature`). A
    payload that does not decode to a dict with exactly
    :data:`SIGNED_FIELDS` is also rejected with ``None``.

    Only those three expected failure shapes are handled. Anything else --
    a missing/invalid ``SECRET_KEY``, a broken settings import -- is left to
    propagate so a real misconfiguration stays visible instead of being
    laundered into an ordinary 400.
    """
    if not token or not isinstance(token, str):
        return None

    try:
        payload = _signer().unsign_object(
            token, max_age=_max_age_seconds()
        )
    except (SignatureExpired, BadSignature):
        return None

    if not isinstance(payload, dict):
        return None
    # Drop the mint nonce before the field-set comparison: the nonce is
    # opaque, is not one of the numbers the token vouches for, and must never
    # reach a caller. A token minted before the nonce existed (no
    # ``NONCE_FIELD`` at all) therefore still verifies.
    payload = {
        key: value
        for key, value in payload.items()
        if key != NONCE_FIELD
    }
    if set(payload) != set(SIGNED_FIELDS):
        return None

    # Rebuild in SIGNED_FIELDS order so the verified payload is exactly the
    # frozen tuple -- same keys, same order -- no matter how it was signed.
    return {field: payload[field] for field in SIGNED_FIELDS}


def hash_result_token(token):
    """Return the 64-char sha256 hexdigest identifying ``token`` (R015).

    This is a *token-identity* hash, not a secret: the token is already
    unforgeable without the signing key, and the digest only needs to be
    collision-free enough to enforce one-submission-per-token. It is a hash
    rather than the token string itself only because the database column is
    bounded (``CharField(max_length=64)``), so a 64-char hexdigest fits.

    **The whole token is hashed, not just its payload segment.** Django's
    ``TimestampSigner`` builds a token as
    ``<base64ed payload>:<base62 timestamp>:<signature>``; hashing only the
    payload would make two legitimate runs of the *same* parameters at
    different times hash identically, so the replay guard (S05-T03) would
    reject a real second attempt as a replay -- directly contradicting the
    slice's "distinct tokens and timestamps are not a false replay"
    requirement. Including the timestamp and signature in the digest makes
    the hash identify the token, which is what the column name and the
    replay guard both mean by it.

    The token is hashed as its own text: the string is the wire form the
    server actually received, so the digest is reproducible from the request
    body alone and does not depend on base64 padding conventions.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()
