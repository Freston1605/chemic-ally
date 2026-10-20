"""Database-level guarantees for ``UserScore.result_token_hash`` (R015, S05-T04).

The replay guard in ``apps/hplc_simulator/views.py`` is a read-then-write:
it checks ``UserScore.objects.filter(result_token_hash=...).exists()`` before
inserting. Under concurrency two requests bearing the same signed token can
both pass that pre-check, so the *database* unique constraint -- not the
``.exists()`` call -- is what makes exactly one of them win. These tests pin
that constraint, its NULL semantics, and the digest contract so a future
migration cannot silently drop them.

``result_token_hash`` is deliberately absent from ``UserScoreSerializer``: a
hash of a token is not useful to a client and is a correlation identifier, so
test (d) also pins that it never leaks onto the wire.

The migration guard reads ``0004_add_result_token_hash.py`` as text under
``SimpleTestCase`` (no DB), so it stays cheap and cannot be skipped by a
database that happens to be unapplied.
"""

import hashlib
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase

from hplc_simulator.models import Level, UserScore
from hplc_simulator.serializers import UserScoreSerializer
from hplc_simulator.tokens import hash_result_token

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'apps'
    / 'hplc_simulator'
    / 'migrations'
    / '0004_add_result_token_hash.py'
)


@pytest.fixture
def hash_level(db):
    """Minimal level for building UserScore rows directly at the DB layer."""
    return Level.objects.create(
        name='Hash Level',
        slug='hash-level',
        description='Level used by result_token_hash tests',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
        is_active=True,
    )


def _score_row(level, session_key, token_hash):
    """Build (but do not save) a minimal valid UserScore row.

    The three JSON config fields are NOT NULL on the model, so they carry
    representative values rather than None.
    """
    return UserScore(
        level=level,
        session_key=session_key,
        mobile_phase={
            'start_b': 5, 'end_b': 95, 'ramp_time': 20, 'ph': 3.0,
        },
        column_config={
            'chemistry': 'C18',
            'length_mm': 150,
            'id_mm': 4.6,
            'particle_size_um': 5.0,
        },
        operation_config={
            'flow_rate_ml_min': 1.0,
            'temperature_c': 30,
            'injection_volume_ul': 10,
        },
        total_run_time=25.0,
        max_pressure_bar=120.0,
        min_resolution=1.8,
        score=400.0,
        is_successful=True,
        overpressure=False,
        result_token_hash=token_hash,
    )


class TestResultTokenHashUniqueness:
    """The unique column that makes a replayed token detectable."""

    def test_duplicate_non_null_hash_is_rejected(self, db, hash_level):
        """Two rows may not share a non-null result_token_hash."""
        _score_row(hash_level, 'session-a', 'a' * 64).save()

        # A nested atomic() marks the outer test transaction for rollback on
        # IntegrityError, so the surrounding test transaction stays usable and
        # the failure does not poison the rest of the test database.
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _score_row(hash_level, 'session-b', 'a' * 64).save()

    def test_multiple_null_hashes_coexist(self, db, hash_level):
        """NULL hashes do not collide: legacy rows and the D006 path stay valid.

        PostgreSQL and SQLite both treat NULLs as distinct under a unique
        index, which is what lets pre-migration rows and any degraded path
        that cannot fingerprint a token keep recording scores.
        """
        _score_row(hash_level, 'session-null-1', None).save()
        _score_row(hash_level, 'session-null-2', None).save()

        assert UserScore.objects.filter(
            result_token_hash__isnull=True
        ).count() == 2

    def test_saved_hash_equals_hash_result_token(self, db, hash_level):
        """A stored hash is exactly ``hash_result_token(token)`` for its token."""
        token = 'payload:timestamp:signature'
        row = _score_row(hash_level, 'session-hash', hash_result_token(token))
        row.full_clean(validate_unique=False)
        row.save()

        row.refresh_from_db()
        assert row.result_token_hash == hashlib.sha256(
            token.encode('utf-8')
        ).hexdigest()
        assert len(row.result_token_hash) == 64
        assert row.result_token_hash == hash_result_token(token)

    def test_serializer_never_exposes_result_token_hash(self, db, hash_level):
        """The serialized score payload must not leak the token identity."""
        row = _score_row(hash_level, 'session-serializer', 'c' * 64)
        row.save()

        data = UserScoreSerializer(row).data
        assert 'result_token_hash' not in data


class TestResultTokenHashMigration(SimpleTestCase):
    """Durable guard that migration 0004 keeps the unique column."""

    def test_migration_adds_unique_result_token_hash(self):
        assert MIGRATION_PATH.exists(), (
            f'expected migration at {MIGRATION_PATH}'
        )
        source = MIGRATION_PATH.read_text(encoding='utf-8')

        assert "model_name='userscore'" in source
        assert "name='result_token_hash'" in source
        assert 'AddField' in source
        assert 'unique=True' in source
        # The dependency must chain onto the previous migration so the graph
        # stays linear and rollback to 0003 remains valid.
        assert "('hplc_simulator', '0003_add_analyte_response_fields')" in source
