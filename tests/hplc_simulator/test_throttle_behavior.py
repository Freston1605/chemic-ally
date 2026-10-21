"""Runtime behaviour of the M006/S02 scoped throttle backstop (R027, R028).

``config/settings/base.py`` puts Django's ``DatabaseCache`` behind DRF's
``ScopedRateThrottle`` and gives the two expensive/write-heavy views their own
scopes ('simulate' and 'scores'). The configuration is asserted in
``test_throttle_config.py``; THIS file proves the wiring actually bites at
runtime:

* the simulate endpoint really answers 429 once its budget is spent;
* the two scopes really are independent (a simulate flood does not starve the
  scores endpoint);
* two different client identities really get their own budget;
* the counters really live in the shared database cache, not in process memory
  (the per-container pitfall that would multiply the effective limit by the
  number of warm Lambda containers).

The requests use an empty body on purpose: DRF checks the throttle in
``initial()``, before the handler, so an invalid payload still consumes a
budget slot -- but it short-circuits before the LSS engine runs, which keeps
the burst fast.
"""

import pytest
from django.db import connection

from hplc_simulator.models import Level

# The suite-wide "do not throttle the tests" override must NOT apply here:
# this module is the evidence that the configured throttle really bites.
pytestmark = pytest.mark.throttle_enabled

SIMULATE_URL = '/hplc/api/simulate/'
SCORES_URL = '/hplc/api/scores/'
SIMULATE_BUDGET = 60
SCORES_BUDGET = 30


@pytest.fixture
def throttled_level(db):
    """An active level so the API views resolve their routes."""
    return Level.objects.create(
        name='Throttle Level',
        slug='throttle-level',
        description='Level used by the throttle behaviour tests',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
        is_active=True,
    )


def _client_for(ip):
    """An API client whose identity is *ip* (NUM_PROXIES=1 -> last XFF hop)."""
    from rest_framework.test import APIClient

    # ``**defaults`` seeds the WSGI environ of every request this client
    # makes, so the forwarded-for hop and the peer address are fixed for the
    # whole burst.
    return APIClient(
        HTTP_X_FORWARDED_FOR=ip,
        REMOTE_ADDR='10.0.0.1',
    )


def _burst(client, url, count):
    return [client.post(url, {}, format='json').status_code for _ in range(count)]


@pytest.mark.django_db
def test_simulate_endpoint_throttles_after_its_budget(throttled_level):
    """A burst past the 'simulate' budget is answered with 429."""
    client = _client_for('203.0.113.7')
    codes = _burst(client, SIMULATE_URL, SIMULATE_BUDGET + 5)

    assert codes[:SIMULATE_BUDGET].count(429) == 0, (
        'the throttle fired before the budget was spent: %r' % codes
    )
    assert codes[SIMULATE_BUDGET:].count(429) == 5, (
        'expected %d throttled responses, got %r' % (5, codes)
    )


@pytest.mark.django_db
def test_throttled_response_carries_retry_after(throttled_level):
    """A 429 tells the client when to come back."""
    client = _client_for('203.0.113.8')
    _burst(client, SIMULATE_URL, SIMULATE_BUDGET)
    response = client.post(SIMULATE_URL, {}, format='json')

    assert response.status_code == 429
    assert 'Retry-After' in response


@pytest.mark.django_db
def test_scopes_are_independent(throttled_level):
    """A simulate flood must not consume the scores budget."""
    client = _client_for('203.0.113.9')
    _burst(client, SIMULATE_URL, SIMULATE_BUDGET + 5)

    scores_codes = _burst(client, SCORES_URL, SCORES_BUDGET)
    assert 429 not in scores_codes, (
        'the scores scope was starved by the simulate flood: %r' % scores_codes
    )


@pytest.mark.django_db
def test_each_client_identity_gets_its_own_budget(throttled_level):
    """Two client identities do not share a counter."""
    saturated = _client_for('198.51.100.9')
    _burst(saturated, SIMULATE_URL, SIMULATE_BUDGET + 1)
    assert saturated.post(SIMULATE_URL, {}, format='json').status_code == 429

    fresh = _client_for('198.51.100.10')
    assert fresh.post(SIMULATE_URL, {}, format='json').status_code != 429


@pytest.mark.django_db
def test_throttle_state_lives_in_the_shared_database_cache(throttled_level):
    """The counters are rows in the cache table, so every container sees them."""
    client = _client_for('203.0.113.11')
    _burst(client, SIMULATE_URL, 3)

    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM django_cache')
        rows = cursor.fetchone()[0]

    assert rows > 0, (
        'no throttle state was written to the shared cache table -- the '
        'counters are per-process, so the limit would be multiplied by the '
        'number of warm containers'
    )
