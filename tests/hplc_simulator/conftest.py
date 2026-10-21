"""Shared fixtures for HPLC simulator API endpoint tests (R006).

These fixtures are the single source of truth for the ``/hplc/api/*`` surface
frozen by S02. Later tasks in S03 (simulate, scores, progress) reuse
``api_level_with_analytes``, ``valid_simulate_payload``, and
``valid_score_payload`` instead of hand-rolling payloads, so the request shapes
stay identical to what ``static/hplc_simulator/js/simulator.js`` actually sends.

No fixture touches the database at import time: every DB-backed fixture
declares ``db`` explicitly so collection of the whole suite stays cheap.
"""

import time
from unittest import mock

import django.core.signing
import pytest
from django.core.cache import cache
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework.views import APIView

from hplc_simulator.models import Analyte, Level


def _database_access_allowed(request):
    """False when the test's class forbids database access.

    ``django.test.SimpleTestCase`` blocks every query on the databases it
    does not declare, so the cache clear has to be skipped for those tests.
    They never reach the throttled API, so no throttle state can leak into
    them.
    """
    cls = getattr(request.node, "cls", None)
    if cls is None or not issubclass(cls, SimpleTestCase):
        return True
    databases = getattr(cls, "databases", None)
    if databases == "__all__":
        return True
    return "default" in (databases or ())


@pytest.fixture(scope="session")
def _django_cache_table(django_db_setup, django_db_blocker):
    """Create Django's cache table in the test database (M006/S02).

    ``base.py`` points Django's cache at ``DatabaseCache`` on the default
    database, so every cache operation in this suite is a real database
    round-trip. Django does NOT create that table with ``migrate`` (it ships
    it as the ``createcachetable`` management command) and the test database
    is built from migrations, so the table is created here -- once per
    session, before any test touches the cache.
    """
    from django.core.management import call_command

    with django_db_blocker.unblock():
        call_command("createcachetable", verbosity=0)


@pytest.fixture(autouse=True)
def _clear_drf_cache(request, _django_cache_table, django_db_blocker):
    """Clear Django's cache around every test in this package.

    DRF throttles are cache-backed. S06 will add ``ScopedRateThrottle`` to the
    API views; without a clean cache, request counters could leak between tests
    and produce a self-inflicted 429 flake. Clearing before *and* after keeps
    endpoint tests deterministic and makes any 429 a genuine regression rather
    than cross-test pollution.

    The cache is the database-backed one (M006/S02), so the clear is a real
    DELETE: it runs with the pytest-django DB blocker released, which keeps
    the suite working for the tests that do not request the ``db`` fixture.
    Tests whose class forbids database access (``SimpleTestCase``) skip the
    clear entirely -- they never call the throttled API.
    """
    if not _database_access_allowed(request):
        yield
        return
    with django_db_blocker.unblock():
        cache.clear()
    yield
    with django_db_blocker.unblock():
        cache.clear()


@pytest.fixture(autouse=True)
def disable_drf_throttling(request):
    """Keep the suite from throttling itself (M006/S02, R015 / legacy TEST-05).

    ``config/settings/base.py`` installs DRF's ``ScopedRateThrottle`` on the
    simulate and scores endpoints. Without this override a test that posts to
    those endpoints more than once per minute would answer its own requests
    with a 429 -- a self-inflicted failure that says nothing about the code
    under test, and a flake whose trigger (how many requests the suite happens
    to make) drifts with every new test file.

    The override is applied to the whole ``REST_FRAMEWORK`` mapping with ONLY
    ``DEFAULT_THROTTLE_CLASSES`` emptied, so every other DRF setting (the
    exception handler, the renderer classes, the pagination) stays exactly as
    shipped. DRF listens for Django's ``setting_changed`` signal and reloads
    its ``api_settings`` on every override, so the change takes effect for the
    next request without a restart.

    The settings override alone is NOT enough, and that is the whole reason
    this fixture exists. DRF binds ``APIView.throttle_classes`` from
    ``api_settings.DEFAULT_THROTTLE_CLASSES`` when the class body is
    evaluated -- i.e. once, at import time. Overriding ``REST_FRAMEWORK``
    afterwards changes the *setting*, but the already-imported view classes
    keep the throttle they were built with, and the suite throttles itself
    exactly as before. The fixture therefore also patches the bound class
    attribute, which is what actually reaches ``get_throttles()``.

    The dedicated throttle modules opt back in with the ``throttle_enabled``
    marker (``pytestmark = pytest.mark.throttle_enabled``): they are the
    evidence that the throttle really bites, so they must run against the
    configured throttle rather than a disabled one.
    """
    if request.node.get_closest_marker('throttle_enabled'):
        yield
        return

    patched = dict(settings.REST_FRAMEWORK)
    patched['DEFAULT_THROTTLE_CLASSES'] = []
    with override_settings(REST_FRAMEWORK=patched), mock.patch.object(
        APIView, 'throttle_classes', []
    ):
        yield


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
def fresh_result_token(api_level):
    """Return a factory minting valid result tokens for ``api_level``.

    S05-T03 made ``POST /hplc/api/scores/`` accept only server-signed
    numbers, so any score test now needs a token. Minting one here -- through
    the same ``sign_result_token`` the simulate endpoint uses, never a
    hand-rolled HMAC -- is what lets the pre-existing score tests be UPDATED
    in place instead of deleted, keeping their status/score_id/progress
    assertions as the regression evidence that the write path still works.

    A factory (rather than a single token) is required by the tests that
    submit more than once: a repeated identical token is now a replay 400, so
    each submission needs its own token.

    **Each call returns a token with a DISTINCT identity.**
    ``TimestampSigner`` stamps to whole seconds, so two calls inside one second
    would otherwise serialize byte-identically -- same payload, same timestamp,
    same signature -- and the replay guard (S05-T03/T04) would refuse the
    second legitimate submission as a replay. Rewriting the token text is not
    an option: the timestamp is inside the signed region, so a hand-shifted
    token stops verifying. The factory therefore advances the signer's own
    clock by the call counter, so each token is genuinely signed at a distinct
    second and still verifies against the real verifier.
    """
    from hplc_simulator.tokens import sign_result_token

    counter = {'n': 0}

    def _make(**overrides):
        payload = {
            'level_id': api_level.id,
            'total_run_time': 24.5,
            'max_pressure_bar': 120.0,
            'min_resolution': 1.8,
            'critical_pair': ['Caffeine', 'Acetaminophen'],
            'overpressure': False,
        }
        payload.update(overrides)
        counter['n'] += 1
        # Advance the clock that ``TimestampSigner.timestamp()`` reads. Django
        # resolves ``time.time()`` from its own module global, so patching the
        # signer instance would have no effect; the module attribute is the
        # source of truth. The offset is small enough that every token still
        # verifies inside ``SCORE_TOKEN_MAX_AGE_SECONDS``.
        offset = counter['n']
        real_time = time.time
        django.core.signing.time.time = lambda: real_time() + offset
        try:
            return sign_result_token(payload)
        finally:
            django.core.signing.time.time = real_time

    return _make


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
        # D022: ``score``/``is_successful``/``overpressure`` above are kept so
        # the payload still matches what simulator.js sends verbatim (they are
        # accepted-and-ignored). ``result_token`` is the only trusted field and
        # is overwritten per-test via the ``valid_score_payload`` factory
        # below; the placeholder here keeps the dict shape honest.
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
