import pytest
from django.db import IntegrityError
from hplc_simulator.models import Level, UserScore, LevelProgress


@pytest.fixture
def sample_level(db):
    return Level.objects.create(
        name='Test Level',
        slug='test-level',
        description='Test',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
    )


@pytest.fixture
def valid_score_data():
    return {
        'mobile_phase': {'start_b': 5, 'end_b': 95, 'ramp_time': 20, 'ph': 3.0},
        'column_config': {
            'chemistry': 'C18', 'length_mm': 150,
            'id_mm': 4.6, 'particle_size_um': 5.0,
        },
        'operation_config': {
            'flow_rate_ml_min': 1.0, 'temperature_c': 30,
            'injection_volume_ul': 10,
        },
        'total_run_time': 25.0,
        'max_pressure_bar': 120.0,
        'min_resolution': 1.8,
        'score': 400.0,
        'is_successful': True,
        'overpressure': False,
    }


class TestUserScoreConstraints:
    """Test CHECK constraints on UserScore model."""

    def test_negative_pressure_rejected(self, db, sample_level, valid_score_data):
        """Pressure cannot be negative."""
        valid_score_data['max_pressure_bar'] = -10.0
        with pytest.raises(IntegrityError):
            UserScore.objects.create(
                level=sample_level,
                session_key='test-session-1',
                **valid_score_data,
            )

    def test_negative_resolution_rejected(self, db, sample_level, valid_score_data):
        """Resolution cannot be negative."""
        valid_score_data['min_resolution'] = -0.5
        with pytest.raises(IntegrityError):
            UserScore.objects.create(
                level=sample_level,
                session_key='test-session-2',
                **valid_score_data,
            )

    def test_negative_run_time_rejected(self, db, sample_level, valid_score_data):
        """Run time cannot be negative."""
        valid_score_data['total_run_time'] = -5.0
        with pytest.raises(IntegrityError):
            UserScore.objects.create(
                level=sample_level,
                session_key='test-session-3',
                **valid_score_data,
            )

    def test_negative_score_rejected(self, db, sample_level, valid_score_data):
        """Score cannot be negative."""
        valid_score_data['score'] = -100.0
        with pytest.raises(IntegrityError):
            UserScore.objects.create(
                level=sample_level,
                session_key='test-session-4',
                **valid_score_data,
            )

    def test_zero_values_accepted(self, db, sample_level, valid_score_data):
        """Zero values are physically valid (no signal, no resolution)."""
        valid_score_data['max_pressure_bar'] = 0.0
        valid_score_data['min_resolution'] = 0.0
        valid_score_data['total_run_time'] = 0.0
        valid_score_data['score'] = 0.0
        score = UserScore.objects.create(
            level=sample_level,
            session_key='test-session-5',
            **valid_score_data,
        )
        assert score.max_pressure_bar == 0.0
        assert score.min_resolution == 0.0
        assert score.score == 0.0

    def test_valid_score_accepted(self, db, sample_level, valid_score_data):
        """Valid positive values should be accepted."""
        score = UserScore.objects.create(
            level=sample_level,
            session_key='test-session-6',
            **valid_score_data,
        )
        assert score.score == 400.0
        assert score.is_successful is True


class TestLevelProgressConstraints:
    """Test CHECK constraints on LevelProgress model."""

    def test_negative_best_score_rejected(self, db, sample_level):
        """Best score cannot be negative."""
        with pytest.raises(IntegrityError):
            LevelProgress.objects.create(
                level=sample_level,
                session_key='test-session-7',
                best_score=-500.0,
            )

    def test_negative_best_resolution_rejected(self, db, sample_level):
        """Best resolution cannot be negative."""
        with pytest.raises(IntegrityError):
            LevelProgress.objects.create(
                level=sample_level,
                session_key='test-session-8',
                best_resolution=-1.0,
            )

    def test_negative_best_run_time_rejected(self, db, sample_level):
        """Best run time cannot be negative."""
        with pytest.raises(IntegrityError):
            LevelProgress.objects.create(
                level=sample_level,
                session_key='test-session-9',
                best_run_time=-10.0,
            )

    def test_zero_progress_accepted(self, db, sample_level):
        """Zero values are valid for initial progress."""
        progress = LevelProgress.objects.create(
            level=sample_level,
            session_key='test-session-10',
            best_score=0.0,
            best_resolution=0.0,
            best_run_time=0.0,
        )
        assert progress.best_score == 0.0
