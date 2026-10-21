"""The test suite must never throttle itself (M006/S02, R015 / legacy TEST-05).

``config/settings/base.py`` installs a ``ScopedRateThrottle`` with a 60/min
budget on the simulate endpoint and a 30/min budget on the scores endpoint.
Without the suite-wide override in ``conftest.py``, a test that posts to
those endpoints repeatedly would answer its own requests with a 429 -- a
self-inflicted failure unrelated to the code under test, whose trigger (how
many requests the suite happens to make) drifts with every new test file.

This module is the evidence for that override:

* the override is in force for a normal test (no throttle classes);
* a burst well past the configured budget is NOT throttled;
* and -- the control -- the very same burst IS throttled when the configured
  throttle is put back, so the burst really does exceed the limit and the
  first two assertions are not passing vacuously.
"""

from unittest import mock

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from hplc_simulator.models import Level

SIMULATE_BUDGET = 60
BURST_SIZE = 70


@pytest.fixture
def api_level(db):
    """An active level so the simulate route resolves."""
    return Level.objects.create(
        name='Suite Level',
        slug='suite-level',
        description='Level used by the suite-throttle-isolation tests',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
        is_active=True,
    )


def _client_for(ip):
    return APIClient(HTTP_X_FORWARDED_FOR=ip, REMOTE_ADDR='10.0.0.1')


def _burst(client, count):
    url = reverse('hplc_simulator:api-simulate')
    return [
        client.post(url, {}, format='json').status_code for _ in range(count)
    ]


def test_the_suite_override_empties_the_throttle_classes():
    """A normal test runs with the throttle switched off."""
    assert settings.REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] == [], (
        'the suite-wide override is not in force; a test that bursts the '
        'simulate endpoint would 429 itself'
    )


@pytest.mark.django_db
def test_a_burst_past_the_budget_is_not_throttled(api_level):
    """More requests than the configured budget still succeed (not 429)."""
    codes = _burst(_client_for('203.0.113.201'), BURST_SIZE)

    assert 429 not in codes, (
        'the suite throttled itself: %d of %d requests were answered 429'
        % (codes.count(429), BURST_SIZE)
    )


@pytest.mark.django_db
def test_the_same_burst_is_throttled_when_the_throttle_is_active(api_level):
    """The control: with the configured throttle back, the burst is refused.

    Without this case the two tests above would pass even if the burst were
    far below the limit, and the override would be untested.
    """
    configured = dict(settings.REST_FRAMEWORK)
    configured['DEFAULT_THROTTLE_CLASSES'] = [
        'rest_framework.throttling.ScopedRateThrottle',
    ]

    # Both halves are needed: the SETTING (for anything that reads
    # ``api_settings`` at request time) and the class attribute DRF bound at
    # import time (what ``get_throttles()`` actually uses).
    with override_settings(REST_FRAMEWORK=configured), mock.patch.object(
        APIView, 'throttle_classes', [ScopedRateThrottle]
    ):
        codes = _burst(_client_for('203.0.113.202'), BURST_SIZE)

    assert codes[:SIMULATE_BUDGET].count(429) == 0, (
        'the throttle fired before the budget was spent: %r' % codes
    )
    assert codes[SIMULATE_BUDGET:].count(429) == BURST_SIZE - SIMULATE_BUDGET, (
        'the burst did not exceed the %d/min budget: %r'
        % (SIMULATE_BUDGET, codes)
    )
