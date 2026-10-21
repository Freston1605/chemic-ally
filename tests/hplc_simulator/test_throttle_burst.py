"""429 burst regression test for the scoped throttles (M006/S04, R030/THROT-05).

``test_throttle_config.py`` proves the throttle is *configured* and
``test_throttle_behavior.py`` proves it fires on a cheap, invalid request.
THIS module is the end-to-end burst regression: it clears the throttle cache,
drives a real burst of **valid** requests through the throttled endpoints and
observes a genuine 429 -- while every request issued inside the budget still
answers 200/201.

Why "valid" requests matter
---------------------------
A burst of malformed requests answers 400 long before the throttle is asked
anything, so a test that only counts status codes can pass for the wrong
reason (the request was rejected, not throttled). Every request in this
module is therefore a request the server would have *served*:

* the simulate burst posts the exact ``SimulationRequestSerializer`` shape the
  browser sends, so the within-budget responses are real 200s carrying a
  chromatogram and a server-signed ``result_token``;
* the scores burst obtains a fresh, valid token from the normal simulate
  flow (the endpoint refuses a bare score since D022/S05) and posts it, so
  the within-budget responses are real 201s that actually write a row.

The throttled response is then asserted to be the throttle's 429 and not
"some" failure: status 429, DRF's throttle message in the body, and the
``Retry-After`` header. A configuration where the throttle silently does not
engage (a missing scope, a scope without a rate, a broken cache backend)
cannot satisfy this module: the burst would answer 200 all the way through.

Isolation
---------
The throttle keys on the client address (``NUM_PROXIES = 1`` -> the last
``X-Forwarded-For`` hop), so every test here uses its own RFC 5737 address
and never shares a bucket with another test. The cache is cleared before AND
after every test in this module (see ``burst_cache``), so no test inherits a
consumed budget (R015 / legacy TEST-05).
"""

import time

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from hplc_simulator.views import SimulateView

# The suite-wide "do not throttle the tests" override must NOT apply here:
# this module is the evidence that the configured throttle really bites.
pytestmark = pytest.mark.throttle_enabled

SIMULATE_URL = '/hplc/api/simulate/'
SCORES_URL = '/hplc/api/scores/'
LEVELS_URL = '/hplc/api/levels/'

# Distinct RFC 5737 documentation addresses, one per test, so no two tests
# ever share a throttle bucket.
SIMULATE_BURST_IP = '203.0.113.201'
SCORES_BURST_IP = '203.0.113.202'
STARVING_IP = '203.0.113.203'
NEIGHBOUR_IP = '203.0.113.204'
UNTHROTTLED_READ_IP = '203.0.113.205'
BARE_SCORE_IP = '203.0.113.206'


@pytest.fixture(autouse=True)
def burst_cache(db):
    """Start every burst from an empty throttle budget and leave one behind.

    The cache is the database-backed one shared by all Lambda containers, so
    ``clear()`` is a real DELETE of the counter rows. Clearing before the
    burst makes the boundary deterministic; clearing afterwards keeps the
    consumed budget from leaking into the next test (R015 / legacy TEST-05).
    """
    cache.clear()
    yield
    cache.clear()


def _client_for(ip):
    """An API client whose throttle identity is *ip*.

    ``NUM_PROXIES = 1`` makes DRF read the LAST ``X-Forwarded-For`` entry,
    so a single forwarded address is the client identity behind the edge.
    """
    return APIClient(HTTP_X_FORWARDED_FOR=ip, REMOTE_ADDR='10.0.0.1')


def _budget(scope):
    """Return ``(requests, window_seconds)`` for a configured throttle scope.

    The number of requests is read from the SHIPPED settings instead of being
    hard-coded, so the burst always straddles the boundary the deployment
    actually enforces. The window matters too: DRF's throttle history drops
    entries that age out of the window, so a burst that takes longer than the
    window would never trip the limit.
    """
    rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
    assert scope in rates, (
        'the %r scope has no configured rate, so the throttle cannot engage '
        'for it (configured scopes: %r)' % (scope, sorted(rates))
    )
    # The SAME class the views are throttled with, bound to the scope: it
    # resolves the rate out of DRF's own ``THROTTLE_RATES`` mapping and parses
    # it with DRF's own parser, so this reads the shipped limit rather than
    # a copy of it. A scope without a rate raises here -- loudly, instead of
    # letting the burst pass against a throttle that never engages.
    throttle_cls = type('ScopedBudget', (ScopedRateThrottle,), {'scope': scope})
    throttle = throttle_cls()
    return throttle.parse_rate(throttle.get_rate())


def _simulate(client, payload):
    """POST a simulation and return the response."""
    return client.post(SIMULATE_URL, payload, format='json')


def _score(client, level_id, payload, token):
    """POST a score submission carrying a server-signed result token."""
    body = dict(payload)
    body['level_id'] = level_id
    body['result_token'] = token
    return client.post(SCORES_URL, body, format='json')


def _mint_token(client, payload):
    """Run the normal simulate flow and return the signed result token."""
    response = _simulate(client, payload)
    assert response.status_code == 200, (
        'the simulate flow did not mint a token: %s %r'
        % (response.status_code, response.content[:200])
    )
    return response.data['result_token']


def _assert_is_throttle_429(response):
    """Assert the response is the throttle's 429 -- not some other failure."""
    body = response.content.decode()
    assert response.status_code == 429, (
        'expected a throttle 429, got %s with body %r'
        % (response.status_code, body[:200])
    )
    assert 'throttled' in body.lower(), (
        'the 429 body does not identify itself as a throttle rejection: %r'
        % body[:200]
    )
    assert 'Retry-After' in response, (
        'the 429 carries no Retry-After header, so the client cannot know '
        'when to come back'
    )


@pytest.mark.django_db
def test_simulate_burst_returns_a_real_429_after_the_budget(
    valid_simulate_payload
):
    """N valid simulations are served; the (N+1)-th is throttled."""
    budget, window = _budget('simulate')
    client = _client_for(SIMULATE_BURST_IP)

    started = time.monotonic()
    served = [_simulate(client, valid_simulate_payload).status_code
              for _ in range(budget)]
    elapsed = time.monotonic() - started

    assert served == [200] * budget, (
        'legitimate traffic inside the budget was not served with 200: %r'
        % served[:10]
    )
    assert elapsed < window, (
        'the burst took %.1fs, longer than the %.1fs throttle window, so the '
        'counters aged out before the limit was reached'
        % (elapsed, window)
    )

    # The (N+1)-th request is IDENTICAL to the ones that were served, so the
    # only thing that can answer it is the throttle.
    _assert_is_throttle_429(_simulate(client, valid_simulate_payload))


@pytest.mark.django_db
def test_scores_burst_returns_a_real_429_after_the_budget(
    valid_simulate_payload, valid_score_payload
):
    """N valid score submissions are served; the (N+1)-th is throttled.

    Every submission carries a token obtained through the normal simulate
    flow, because the scores endpoint refuses a bare score (D022/S05): a
    burst of unsigned scores would be answered 400 and would prove nothing
    about the throttle.
    """
    scores_budget, window = _budget('scores')
    simulate_budget, _simulate_window = _budget('simulate')
    assert scores_budget + 1 <= simulate_budget, (
        'the scores budget (%d) is not tighter than the simulate budget '
        '(%d): the token-minting simulate flow would be throttled before '
        'the scores burst could be issued'
        % (scores_budget, simulate_budget)
    )

    client = _client_for(SCORES_BURST_IP)
    level_id = valid_simulate_payload['level_id']

    started = time.monotonic()
    responses = []
    for _ in range(scores_budget + 1):
        token = _mint_token(client, valid_simulate_payload)
        responses.append(
            _score(client, level_id, valid_score_payload, token)
        )
    elapsed = time.monotonic() - started

    codes = [response.status_code for response in responses]
    assert codes[:scores_budget] == [201] * scores_budget, (
        'legitimate score submissions inside the budget were not served '
        'with 201: %r' % codes[:10]
    )
    _assert_is_throttle_429(responses[scores_budget])
    assert elapsed < window, (
        'the burst took %.1fs, longer than the %.1fs throttle window, so the '
        'counters aged out before the limit was reached'
        % (elapsed, window)
    )


@pytest.mark.django_db
def test_a_throttled_client_does_not_starve_other_clients(valid_simulate_payload):
    """The burst is scoped to one client identity, not to everyone."""
    budget, _window = _budget('simulate')

    saturating = _client_for(STARVING_IP)
    for _ in range(budget):
        assert _simulate(saturating, valid_simulate_payload).status_code == 200
    assert _simulate(saturating, valid_simulate_payload).status_code == 429

    neighbour = _client_for(NEIGHBOUR_IP)
    response = _simulate(neighbour, valid_simulate_payload)
    assert response.status_code == 200, (
        'a different client identity was starved by the burst: %s'
        % response.status_code
    )


@pytest.mark.django_db
def test_the_burst_does_not_throttle_the_unthrottled_read_endpoints(
    valid_simulate_payload
):
    """The 429 is a scoped throttle, not a blanket failure of the API.

    The level list carries no throttle scope, so it must keep answering 200
    even while the same client identity is being throttled on /simulate/.
    A blanket 429 would also make the "within budget returns 200" assertion
    pass for the wrong reason.
    """
    budget, _window = _budget('simulate')

    client = _client_for(UNTHROTTLED_READ_IP)
    for _ in range(budget):
        assert _simulate(client, valid_simulate_payload).status_code == 200
    assert _simulate(client, valid_simulate_payload).status_code == 429

    listing = client.get(LEVELS_URL)
    assert listing.status_code == 200, (
        'the unthrottled level list answered %s while the client was being '
        'throttled on the simulate scope' % listing.status_code
    )


@pytest.mark.django_db
def test_scores_endpoint_refuses_a_score_without_a_signed_token(
    valid_score_payload
):
    """The scores path really is token-gated (negative case for the burst).

    Without this, a scores "burst" could be satisfied by posting bare
    scores: every request would be answered 400, no row would be written,
    and the test would be asserting nothing about the throttle.
    """
    client = _client_for(BARE_SCORE_IP)

    body = dict(valid_score_payload)
    body.pop('result_token', None)
    response = client.post(SCORES_URL, body, format='json')

    assert response.status_code == 400, (
        'a score without a signed token was accepted: %s %r'
        % (response.status_code, response.content[:200])
    )


@pytest.mark.django_db
def test_simulate_view_declares_the_scope_the_burst_depends_on():
    """Guard the precondition the burst relies on.

    ``ScopedRateThrottle`` is a silent no-op on a view without a
    ``throttle_scope``; this assertion turns that silent misconfiguration
    into a loud failure next to the burst that would otherwise hide it.
    """
    assert SimulateView.throttle_scope == 'simulate'
    assert 'simulate' in settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
