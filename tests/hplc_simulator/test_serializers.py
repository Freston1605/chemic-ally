"""Regression tests for the HPLC simulator serializers.

R002: ``LevelProgressSerializer`` declared ``Meta.model = UserScore`` while its
field list is LevelProgress-only and the LevelProgress model was not imported.
Constructing the serializer with no instance never raised; the failure surfaced
only when a non-empty row was serialized, so /hplc/api/progress/ 500'd for any
returning user with at least one LevelProgress row (and returned [] for a fresh
session). These tests serialize a *non-empty* row on purpose — an empty queryset
passes vacuously and does not exercise the bug.
"""

import pytest

from hplc_simulator.models import Level, LevelProgress
from hplc_simulator.serializers import LevelProgressSerializer


@pytest.fixture
def progress_level(db):
    return Level.objects.create(
        name='Progress Serializer Level',
        slug='progress-serializer-level',
        description='Level used to exercise LevelProgressSerializer',
        difficulty='intermediate',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
    )


class TestLevelProgressSerializer:
    """LevelProgressSerializer must serialize real LevelProgress rows."""

    def test_serializes_non_empty_row(self, db, progress_level):
        """A non-empty LevelProgress row serializes with the declared fields."""
        row = LevelProgress.objects.create(
            level=progress_level,
            session_key='progress-session-1',
            best_score=820.0,
            best_resolution=1.9,
            best_run_time=24.5,
            attempts=3,
            completed=True,
        )

        data = LevelProgressSerializer([row], many=True).data

        assert len(data) == 1
        assert data[0]['best_score'] == pytest.approx(820.0)
        assert data[0]['best_resolution'] == pytest.approx(1.9)
        assert data[0]['best_run_time'] == pytest.approx(24.5)
        assert data[0]['attempts'] == 3
        assert data[0]['completed'] is True
        assert data[0]['level_name'] == progress_level.name
        assert data[0]['difficulty'] == progress_level.difficulty
