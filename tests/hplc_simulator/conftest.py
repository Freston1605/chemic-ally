"""Shared fixtures for HPLC simulator API endpoint tests (R006).

These fixtures are the single source of truth for the ``/hplc/api/*`` surface
frozen by S02. Later tasks in S03 (simulate, scores, progress) reuse
``api_level_with_analytes``, ``valid_simulate_payload``, and
``valid_score_payload`` instead of hand-rolling payloads, so the request shapes
stay identical to what ``static/hplc_simulator/js/simulator.js`` actually sends.

No fixture touches the database at import time: every DB-backed fixture
declares ``db`` explicitly so collection of the whole suite stays cheap.
"""

import pytest
from django.core.cache import cache

from hplc_simulator.models import Analyte, Level


@pytest.fixture(autouse=True)
def _clear_drf_cache():
    """Clear Django's cache around every test in this package.

    DRF throttles are cache-backed. S06 will add ``ScopedRateThrottle`` to the
    API views; without a clean cache, request counters could leak between tests
    and produce a self-inflicted 429 flake. Clearing before *and* after keeps
    endpoint tests deterministic and makes any 429 a genuine regression rather
    than cross-test pollution.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_level(db):
    """Active level with no analytes, used for list/score fixtures."""
    return Level.objects.create(
        name='API Level',
        slug='api-level',
        description='Level used by API endpoint tests',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
        is_active=True,
    )


@pytest.fixture
def api_inactive_level(db):
    """Inactive level: must never appear in list/detail responses."""
    return Level.objects.create(
        name='API Inactive Level',
        slug='api-inactive-level',
        description='Inactive level used by API endpoint tests',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
        is_active=False,
    )


@pytest.fixture
def api_level_with_analytes(db):
    """Active level with three realistic analytes so the real engine runs.

    LSS values are physically plausible for a C18 gradient run, so
    ``generate_chromatogram`` returns a real chromatogram (not an empty one)
    for the simulate endpoint tests.
    """
    level = Level.objects.create(
        name='API Simulate Level',
        slug='api-level-simulate',
        description='Active level with analytes for simulate tests',
        difficulty='intermediate',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
        is_active=True,
    )
    analytes = [
        Analyte.objects.create(
            name='Caffeine',
            formula='C8H10N4O2',
            pka=0.6,
            log_p=-0.07,
            log_kw=-1.0,
            s_parameter=2.5,
            molecular_weight=194.19,
            neutral_charge=False,
        ),
        Analyte.objects.create(
            name='Acetaminophen',
            formula='C8H9NO2',
            pka=9.38,
            log_p=0.46,
            log_kw=-0.3,
            s_parameter=2.8,
            molecular_weight=151.16,
            neutral_charge=False,
        ),
        Analyte.objects.create(
            name='Ibuprofen',
            formula='C13H18O2',
            pka=4.91,
            log_p=3.97,
            log_kw=0.4,
            s_parameter=3.0,
            molecular_weight=206.29,
            neutral_charge=False,
        ),
    ]
    level.analytes.set(analytes)
    return level


@pytest.fixture
def valid_simulate_payload(api_level_with_analytes):
    """Exact ``SimulationRequestSerializer`` shape the browser client sends."""
    return {
        'level_id': api_level_with_analytes.id,
        'mobile_phase': {
            'start_b': 5,
            'end_b': 95,
            'ramp_time': 20,
            'ph': 3.0,
            'buffer_concentration_mm': 25,
            'is_gradient': True,
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
            'dwell_time_min': 1.0,
        },
    }


@pytest.fixture
def valid_score_payload(api_level):
    """Real score payload from simulator.js ``submitScore`` (< level limit)."""
    return {
        'level_id': api_level.id,
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
