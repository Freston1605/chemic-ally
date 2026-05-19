import pytest
from django.core.exceptions import ValidationError
from hplc_simulator.models import Level, UserScore
from hplc_simulator.serializers import (
    ColumnConfigSerializer,
    OperationConfigSerializer,
    SimulationRequestSerializer,
)


@pytest.fixture
def sample_level(db):
    return Level.objects.create(
        name='Test Level',
        slug='test-level-django',
        description='Test',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
    )


class TestColumnConfigSerializer:
    """Test column chemistry validation."""

    def test_rejects_hilic(self):
        data = {
            'chemistry': 'HILIC',
            'length_mm': 150,
            'id_mm': 4.6,
            'particle_size_um': 5.0,
        }
        serializer = ColumnConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert 'non_field_errors' in serializer.errors

    def test_rejects_normal_phase(self):
        data = {
            'chemistry': 'NP',
            'length_mm': 150,
            'id_mm': 4.6,
            'particle_size_um': 5.0,
        }
        serializer = ColumnConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert 'non_field_errors' in serializer.errors

    def test_accepts_c18(self):
        data = {
            'chemistry': 'C18',
            'length_mm': 150,
            'id_mm': 4.6,
            'particle_size_um': 5.0,
        }
        serializer = ColumnConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_accepts_phenyl(self):
        data = {
            'chemistry': 'Phenyl',
            'length_mm': 150,
            'id_mm': 4.6,
            'particle_size_um': 5.0,
        }
        serializer = ColumnConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


class TestOperationConfigSerializer:
    """Test operation parameter validation."""

    def test_rejects_zero_flow_rate(self):
        data = {
            'flow_rate_ml_min': 0.0,
            'temperature_c': 30,
            'injection_volume_ul': 10,
        }
        serializer = OperationConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert 'flow_rate_ml_min' in serializer.errors

    def test_rejects_negative_flow_rate(self):
        data = {
            'flow_rate_ml_min': -0.5,
            'temperature_c': 30,
            'injection_volume_ul': 10,
        }
        serializer = OperationConfigSerializer(data=data)
        assert not serializer.is_valid()

    def test_accepts_minimum_flow_rate(self):
        data = {
            'flow_rate_ml_min': 0.1,
            'temperature_c': 30,
            'injection_volume_ul': 10,
        }
        serializer = OperationConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors


class TestSimulationRequestSerializer:
    """Test cross-field validation in simulation request."""

    def _valid_payload(self, **overrides):
        payload = {
            'level_id': 1,
            'mobile_phase': {
                'start_b': 5,
                'end_b': 95,
                'ramp_time': 20,
                'ph': 3.0,
                'buffer_concentration_mm': 10,
            },
            'column': {
                'chemistry': 'C18',
                'length_mm': 150,
                'id_mm': 4.6,
                'particle_size_um': 5.0,
            },
            'operation': {
                'flow_rate_ml_min': 1.0,
                'temperature_c': 30,
                'injection_volume_ul': 10,
            },
        }
        payload.update(overrides)
        return payload

    def test_high_ph_on_silica_column_rejected(self):
        data = self._valid_payload(
            mobile_phase={'start_b': 5, 'end_b': 95, 'ramp_time': 20,
                          'ph': 8.0, 'buffer_concentration_mm': 10},
        )
        serializer = SimulationRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert 'mobile_phase' in serializer.errors

    def test_safe_ph_on_silica_accepted(self):
        data = self._valid_payload(
            mobile_phase={'start_b': 5, 'end_b': 95, 'ramp_time': 20,
                          'ph': 7.5, 'buffer_concentration_mm': 10},
        )
        serializer = SimulationRequestSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_high_ph_on_hilic_not_blocked_by_ph_check(self):
        """HILIC is rejected for chemistry, not pH."""
        data = self._valid_payload(
            mobile_phase={'start_b': 5, 'end_b': 95, 'ramp_time': 20,
                          'ph': 8.0, 'buffer_concentration_mm': 10},
            column={'chemistry': 'HILIC', 'length_mm': 150,
                    'id_mm': 4.6, 'particle_size_um': 5.0},
        )
        serializer = SimulationRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert 'column' in serializer.errors


class TestUserScoreClean:
    """Test UserScore.clean() cross-field validation."""

    def test_overpressure_flag_required_when_exceeded(self, db, sample_level):
        """If pressure exceeds level limit, overpressure flag must be set."""
        score = UserScore(
            level=sample_level,
            session_key='test-clean-1',
            mobile_phase={'start_b': 5, 'end_b': 95},
            column_config={'chemistry': 'C18'},
            operation_config={'flow_rate_ml_min': 1.0},
            total_run_time=25.0,
            max_pressure_bar=500.0,
            min_resolution=1.5,
            score=300.0,
            is_successful=True,
            overpressure=False,
        )
        with pytest.raises(ValidationError) as exc_info:
            score.full_clean()
        assert 'overpressure' in exc_info.value.message_dict

    def test_overpressure_accepted_when_flagged(self, db, sample_level):
        """Overpressure is valid when flag is set."""
        score = UserScore(
            level=sample_level,
            session_key='test-clean-2',
            mobile_phase={'start_b': 5, 'end_b': 95},
            column_config={'chemistry': 'C18'},
            operation_config={'flow_rate_ml_min': 1.0},
            total_run_time=25.0,
            max_pressure_bar=500.0,
            min_resolution=0.0,
            score=0.0,
            is_successful=False,
            overpressure=True,
        )
        score.full_clean()

    def test_hilic_column_config_rejected(self, db, sample_level):
        score = UserScore(
            level=sample_level,
            session_key='test-clean-3',
            mobile_phase={'start_b': 5, 'end_b': 95},
            column_config={'chemistry': 'HILIC'},
            operation_config={'flow_rate_ml_min': 1.0},
            total_run_time=25.0,
            max_pressure_bar=120.0,
            min_resolution=1.5,
            score=300.0,
            is_successful=True,
            overpressure=False,
        )
        with pytest.raises(ValidationError) as exc_info:
            score.full_clean()
        assert 'column_config' in exc_info.value.message_dict

    def test_zero_flow_rate_rejected(self, db, sample_level):
        score = UserScore(
            level=sample_level,
            session_key='test-clean-4',
            mobile_phase={'start_b': 5, 'end_b': 95},
            column_config={'chemistry': 'C18'},
            operation_config={'flow_rate_ml_min': 0.0},
            total_run_time=25.0,
            max_pressure_bar=120.0,
            min_resolution=1.5,
            score=300.0,
            is_successful=True,
            overpressure=False,
        )
        with pytest.raises(ValidationError) as exc_info:
            score.full_clean()
        assert 'operation_config' in exc_info.value.message_dict

    def test_valid_score_passes_clean(self, db, sample_level):
        score = UserScore(
            level=sample_level,
            session_key='test-clean-5',
            mobile_phase={'start_b': 5, 'end_b': 95},
            column_config={'chemistry': 'C18'},
            operation_config={'flow_rate_ml_min': 1.0, 'temperature_c': 30,
                              'injection_volume_ul': 10},
            total_run_time=25.0,
            max_pressure_bar=120.0,
            min_resolution=1.5,
            score=300.0,
            is_successful=True,
            overpressure=False,
        )
        score.full_clean()
