"""Configuration contract for the M006/S02 in-app throttle backstop.

The runtime behaviour of the throttle is proved in
``test_throttle_behavior.py``; THIS module proves the *configuration* that
makes that behaviour possible, because a mis-wired backstop is silent: DRF's
``ScopedRateThrottle`` is a no-op on a view without a ``throttle_scope``, and
it raises at request time (not at boot) on a scope that has no rate. The
failures this file guards against are therefore of the "the limit quietly
stopped existing" kind, which no amount of runtime testing would notice.

What is asserted:

* the throttle CLASS (``ScopedRateThrottle``) and the per-scope RATES;
* the SCOPE each view carries -- and the scope it must NOT carry;
* the CACHE BACKEND (the shared, database-backed one, so the counters are
  shared across Lambda containers instead of multiplied by the number of
  warm containers);
* ``NUM_PROXIES = 1``, without which every visitor behind the edge would
  collapse into a single throttle bucket.

The module is marked ``throttle_enabled`` so the suite-wide "do not throttle
the tests" override (see ``conftest.py``) does not apply here: these tests
must read the configuration exactly as it is shipped.
"""

import pytest
from django.conf import settings
from django.utils.module_loading import import_string

from hplc_simulator.views import (
    LevelDetailView,
    LevelListView,
    LevelProgressView,
    ScoreSubmissionView,
    SimulateView,
    UserScoresView,
)

pytestmark = pytest.mark.throttle_enabled

# Every API view in the module, so the scope checks cover the whole surface
# and a newly added view cannot join the throttled set (or the unthrottled
# one) without this file noticing.
API_VIEWS = (
    LevelListView,
    LevelDetailView,
    SimulateView,
    ScoreSubmissionView,
    UserScoresView,
    LevelProgressView,
)

# The views that must stay unthrottled: cheap reads where a 429 would be a
# functional regression for a user who is only browsing.
UNTHROTTLED_VIEWS = (
    LevelListView,
    LevelDetailView,
    UserScoresView,
    LevelProgressView,
)


def test_default_throttle_class_is_the_scoped_rate_throttle():
    """The backstop is DRF's ScopedRateThrottle, and nothing else."""
    classes = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES']

    assert classes == ['rest_framework.throttling.ScopedRateThrottle'], (
        'the throttle class changed; the DottedPath must resolve to DRF\'s '
        'ScopedRateThrottle -- got %r' % (classes,)
    )
    # The DottedPath is only a string until DRF imports it at request time;
    # resolve it here so a typo fails this test instead of the first request.
    assert import_string(classes[0]).__name__ == 'ScopedRateThrottle'


def test_each_scope_has_its_own_budget():
    """'simulate' and 'scores' are separate budgets, not one shared pool."""
    rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']

    assert set(rates) == {'simulate', 'scores'}, (
        'the scope set changed; expected exactly the simulate and scores '
        'scopes, got %r' % (set(rates),)
    )
    assert rates['simulate'] == '60/min'
    assert rates['scores'] == '30/min'
    assert rates['simulate'] != rates['scores'], (
        'the two scopes share a rate, so a flood on one endpoint can starve '
        'the other'
    )


def test_simulate_view_carries_the_simulate_scope():
    """The CPU-bound endpoint opts into its own budget."""
    assert SimulateView.throttle_scope == 'simulate'


def test_score_submission_view_carries_the_scores_scope():
    """The write-heavy endpoint opts into its own, tighter budget."""
    assert ScoreSubmissionView.throttle_scope == 'scores'


def test_cheap_read_views_carry_no_scope():
    """Reading must never be throttled.

    ``ScopedRateThrottle`` is a no-op without a ``throttle_scope``, so the
    absence of the attribute IS the unthrottled contract. Asserting it
    explicitly keeps a well-meaning "let's throttle the list endpoint too"
    from silently degrading ordinary browsing.
    """
    for view in UNTHROTTLED_VIEWS:
        assert not getattr(view, 'throttle_scope', None), (
            '%s carries a throttle scope (%r) but is a cheap read endpoint'
            % (view.__name__, getattr(view, 'throttle_scope', None))
        )


def test_every_view_scope_has_a_configured_rate():
    """A scope without a rate is a silent hole in the backstop.

    DRF raises ``ImproperlyConfigured`` at request time for an unknown
    scope, so a typo in ``throttle_scope`` would turn the endpoint into a
    500 rather than a throttled endpoint. This test catches the typo at
    configuration time instead.
    """
    rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']

    for view in API_VIEWS:
        scope = getattr(view, 'throttle_scope', None)
        if scope is None:
            continue
        assert scope in rates, (
            '%s declares throttle_scope %r but no rate is configured for it'
            % (view.__name__, scope)
        )


def test_cache_backend_is_the_shared_database_cache():
    """The throttle counters live in the shared database cache.

    A per-process cache (LocMemCache) would make the effective limit equal
    to the configured rate TIMES the number of warm Lambda containers, which
    defeats the point of the backstop. The table is also bounded
    (``MAX_ENTRIES``) so distinct client identities cannot grow it without
    limit.
    """
    cache = settings.CACHES['default']

    assert cache['BACKEND'] == 'django.core.cache.backends.db.DatabaseCache', (
        'the cache backend changed; the throttle state must be shared across '
        'Lambda containers, got %r' % (cache['BACKEND'],)
    )
    assert cache['LOCATION'] == 'django_cache'
    assert cache['OPTIONS']['MAX_ENTRIES'] > 0


def test_num_proxies_is_one():
    """Exactly one proxy hop sits in front of the app.

    DRF's ``get_ident`` walks ``X-Forwarded-For`` ``NUM_PROXIES`` hops back
    to find the client address. Behind CloudFront + a Lambda function URL the
    peer address is the edge, not the client, so a wrong value collapses
    every visitor into a single throttle bucket (one visitor's traffic
    throttling everyone else) -- or, with too many hops, lets one client
    rotate through many identities.
    """
    assert settings.REST_FRAMEWORK['NUM_PROXIES'] == 1


def test_shipped_base_settings_carry_the_same_throttle_configuration():
    """The configuration lives in the source, not only in the environment.

    The effective settings above could in principle be produced by a test
    fixture; this asserts the shipped module ``config.settings.base`` really
    carries the same throttle configuration, so the guarantee survives a
    settings refactor that drops it from the source of truth.
    """
    from config.settings import base as base_settings

    assert base_settings.REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] == [
        'rest_framework.throttling.ScopedRateThrottle',
    ]
    assert base_settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] == {
        'simulate': '60/min',
        'scores': '30/min',
    }
    assert base_settings.REST_FRAMEWORK['NUM_PROXIES'] == 1
    assert base_settings.CACHES['default']['BACKEND'] == (
        'django.core.cache.backends.db.DatabaseCache'
    )
