"""API endpoint coverage for the score, history, and progress routes (R006, S03/T03).

Routes under test:
- ``POST /hplc/api/scores/``          (``api-scores``)          -> write path
- ``GET  /hplc/api/scores/history/``  (``api-scores-history``)  -> session read
- ``GET  /hplc/api/progress/``        (``api-progress``)        -> session read

All requests go through ``rest_framework.test.APIClient`` and
``django.urls.reverse`` -- no hardcoded ``/hplc/api/...`` strings -- so the
tests track the frozen URLconf. Fixtures come from the shared
``tests/hplc_simulator/conftest.py`` (S03/T01), so the score request shape
stays identical to what ``static/hplc_simulator/js/simulator.js`` sends.

One ``APIClient`` is used per test so the session cookie persists across the
write and the two read routes. Where a GET needs a pre-existing session, the
client session is primed (``client.session`` saves it and sets the cookie) and
``client.session.session_key`` is used to tie seeded rows to that exact session.

The route 6 regression is the HTTP-level counterpart of the R002 serializer
test: a session that owns a ``LevelProgress`` row must receive a 200 with a
NON-EMPTY row (an empty list would pass vacuously and would not exercise the
defect).

No response in this module is allowed to be a ``429``: the shared autouse
cache-clear fixture makes DRF throttle state deterministic, so a 429 here would
be a genuine regression rather than cross-test pollution.
"""

import copy

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from hplc_simulator.models import LevelProgress, UserScore
from hplc_simulator.simulation.engine import calculate_score


def server_score(total_run_time, api_level, min_resolution=1.8,
                 max_pressure_bar=120.0):
    """The score the SERVER computes for the fixture's signed metrics.

    Derived by calling ``calculate_score`` with the level's own limits rather
    than by hand-arithmetic: the formula has a pressure-penalty term and a
    resolution bonus whose thresholds (``pressure_ratio > 0.8``,
    ``min_resolution >= 1.5``) make a hand-written constant a silent lie if the
    scoring model ever changes. The fixture metrics are 120 bar against a
    400 bar limit with Rs 1.8, i.e. NO penalty and a bonus.
    """
    return calculate_score(
        total_run_time=total_run_time,
        min_resolution=min_resolution,
        max_pressure_bar=max_pressure_bar,
        pressure_limit=api_level.max_pressure_bar,
        base_points=api_level.base_points,
    )[0]


@pytest.fixture
def client():
    return APIClient()


def _scores_url():
    return reverse('hplc_simulator:api-scores')


def _scores_history_url():
    return reverse('hplc_simulator:api-scores-history')


def _progress_url():
    return reverse('hplc_simulator:api-progress')


def _prime_session(client):
    """Save a session and pin its cookie on the client, returning the key."""
    session = client.session
    session.save()
    return client.session.session_key


def _with_token(payload, token):
    """Return ``payload`` plus the server-signed ``result_token`` (D022).

    S05-T03 made the scores endpoint token-authoritative, so every payload
    here must carry a token. The payload dicts are otherwise unchanged --
    including the client-supplied ``score``/``is_successful``/``overpressure``
    which are now accepted-and-ignored -- so these tests keep asserting the
    exact HTTP shape that ``static/hplc_simulator/js/simulator.js`` sends.
    """
    prepared = copy.deepcopy(payload)
    prepared['result_token'] = token
    return prepared


def _fresh_token(fresh_result_token, **overrides):
    """Mint a token whose signed metrics match ``valid_score_payload``."""
    return fresh_result_token(**overrides)


class TestScoreSubmissionEndpoint:
    """``POST /hplc/api/scores/`` records a score and upserts progress."""

    def test_valid_payload_records_score_and_progress(
        self, client, api_level, valid_score_payload, fresh_result_token
    ):
        session_key = _prime_session(client)

        response = client.post(
            _scores_url(),
            _with_token(valid_score_payload, _fresh_token(fresh_result_token)),
            format='json',
        )

        assert response.status_code == 201
        assert response.status_code != 429

        body = response.json()
        assert body['status'] == 'score recorded'
        assert isinstance(body['score_id'], int)

        scores = UserScore.objects.filter(
            level=api_level, session_key=session_key
        )
        assert scores.count() == 1
        # The stored score is the SERVER-computed value for these metrics --
        # NOT the client-supplied 8500.0 that rode along in the payload.
        assert scores.first().score == pytest.approx(
            server_score(24.5, api_level)
        )
        assert scores.first().score != pytest.approx(8500.0)

        progress = LevelProgress.objects.filter(
            level=api_level, session_key=session_key
        )
        assert progress.count() == 1
        record = progress.first()
        assert record.attempts == 1
        assert record.completed is True

    def test_repeat_payload_upserts_same_progress_row(
        self, client, api_level, valid_score_payload, fresh_result_token
    ):
        """Covers the ``if not created`` branch of the progress upsert.

        Each submission carries its OWN fresh token: replaying one token is a
        400 by design (S05-T05 owns that assertion), while two legitimate
        submissions of the same metrics are two attempts on ONE progress row.
        """
        session_key = _prime_session(client)

        first = client.post(
            _scores_url(),
            _with_token(valid_score_payload, _fresh_token(fresh_result_token)),
            format='json',
        )
        assert first.status_code == 201
        assert first.status_code != 429

        second = client.post(
            _scores_url(),
            _with_token(valid_score_payload, _fresh_token(fresh_result_token)),
            format='json',
        )
        assert second.status_code == 201
        assert second.status_code != 429

        progress = LevelProgress.objects.filter(
            level=api_level, session_key=session_key
        )
        # The upsert must not create a second row: unique (level, session_key).
        assert progress.count() == 1
        assert progress.first().attempts == 2

    def test_higher_score_raises_best_score(
        self, client, api_level, valid_score_payload, fresh_result_token
    ):
        session_key = _prime_session(client)

        client.post(
            _scores_url(),
            _with_token(valid_score_payload, _fresh_token(fresh_result_token)),
            format='json',
        )

        # A better run: same level, longer stability and a SHORTER run time.
        # ``score = base_points / total_run_time``, so the shorter run is the
        # higher score -- and the client's own ``score`` field cannot move it.
        better = copy.deepcopy(valid_score_payload)
        better['score'] = 9200.0
        better['total_run_time'] = 10.0
        response = client.post(
            _scores_url(),
            _with_token(
                better,
                _fresh_token(fresh_result_token, total_run_time=10.0),
            ),
            format='json',
        )

        assert response.status_code == 201
        assert response.status_code != 429

        progress = LevelProgress.objects.get(
            level=api_level, session_key=session_key
        )
        assert progress.attempts == 2
        assert progress.best_score == pytest.approx(
            server_score(10.0, api_level)
        )

    def test_overpressure_without_flag_returns_400_with_errors(
        self, client, api_level, valid_score_payload, fresh_result_token
    ):
        """Covers ScoreSubmissionView's ``full_clean`` ValidationError branch.

        ``max_pressure_bar`` is valid for the request serializer (>= 0) but
        exceeds the level limit while ``overpressure`` is False, so the model
        ``clean()`` rejects it and the view maps it to a 400 ``errors`` body.

        NOTE: with S05-T03's signed range check in place, an over-limit
        pressure never reaches ``full_clean`` (the uniform 400 wins first), so
        this test exercises the reachable equivalent: the model's own invariant
        still rejects an over-limit pressure when the signed flag is False.
        """
        payload = copy.deepcopy(valid_score_payload)
        payload['max_pressure_bar'] = api_level.max_pressure_bar + 500.0
        payload['overpressure'] = False

        response = client.post(
            _scores_url(),
            _with_token(
                payload,
                _fresh_token(
                    fresh_result_token,
                    max_pressure_bar=api_level.max_pressure_bar + 500.0,
                ),
            ),
            format='json',
        )

        assert response.status_code == 400
        assert response.status_code != 429
        assert response.status_code != 500

        body = response.json()
        # Either the signed range check or the model invariant refuses it, and
        # neither discloses internal detail nor accepts the impossible run.
        assert 'error' in body or 'errors' in body
        assert UserScore.objects.count() == 0

    def test_missing_trusted_key_returns_400_with_errors(
        self, client, valid_score_payload, fresh_result_token
    ):
        """A dropped TRUSTED field is a 400; a dropped IGNORED field is not.

        ``score``/``is_successful``/``overpressure`` became
        accepted-and-ignored legacy fields (D022), so their absence must NOT
        break the deployed client. ``mobile_phase`` is still a trusted field
        (the model's ``clean()`` validates it), so its absence must 400.
        """
        without_score = copy.deepcopy(valid_score_payload)
        del without_score['score']

        lenient = client.post(
            _scores_url(),
            _with_token(without_score, _fresh_token(fresh_result_token)),
            format='json',
        )
        assert lenient.status_code == 201, lenient.content
        assert lenient.status_code != 429

        strict = client.post(
            _scores_url(),
            _with_token(
                valid_score_payload, _fresh_token(fresh_result_token)
            ),
            format='json',
        )
        assert strict.status_code == 201, strict.content

        missing_trusted = copy.deepcopy(valid_score_payload)
        del missing_trusted['mobile_phase']
        response = client.post(
            _scores_url(),
            _with_token(
                missing_trusted, _fresh_token(fresh_result_token)
            ),
            format='json',
        )

        assert response.status_code == 400
        assert response.status_code != 429
        assert 'errors' in response.json()

    def test_unknown_level_returns_404(
        self, client, valid_score_payload, fresh_result_token
    ):
        """An unknown level keeps 404 semantics.

        The level_id must agree with the signed payload to get this far, so the
        token is minted over the unknown id: an unknown level is not a
        forgery signal and must not be laundered into the uniform 400.
        """
        unknown_id = 999999
        payload = copy.deepcopy(valid_score_payload)
        payload['level_id'] = unknown_id

        response = client.post(
            _scores_url(),
            _with_token(
                payload, _fresh_token(fresh_result_token, level_id=unknown_id)
            ),
            format='json',
        )

        assert response.status_code == 404
        assert response.status_code != 429


class TestScoreHistoryEndpoint:
    """``GET /hplc/api/scores/history/`` reads the calling session's scores."""

    def test_fresh_session_returns_empty_list(self, client, db):
        response = client.get(_scores_history_url())

        assert response.status_code == 200
        assert response.status_code != 429
        assert response.json() == []

    def test_recorded_score_appears_in_history(
        self, client, api_level, valid_score_payload, fresh_result_token
    ):
        client.post(
            _scores_url(),
            _with_token(valid_score_payload, _fresh_token(fresh_result_token)),
            format='json',
        )

        response = client.get(_scores_history_url())

        assert response.status_code == 200
        assert response.status_code != 429

        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 1

        item = body[0]
        for field in (
            'id',
            'level_name',
            'difficulty',
            'score',
            'min_resolution',
            'is_successful',
        ):
            assert field in item
        assert item['level_name'] == api_level.name
        assert item['difficulty'] == api_level.difficulty
        # Server-computed, never the ignored client-supplied 8500.0 that rode
        # along in the payload.
        assert item['score'] == pytest.approx(server_score(24.5, api_level))
        assert item['min_resolution'] == pytest.approx(1.8)
        assert item['is_successful'] is True


class TestLevelProgressEndpoint:
    """``GET /hplc/api/progress/`` reads the calling session's best progress."""

    def test_fresh_session_returns_empty_list(self, client, db):
        response = client.get(_progress_url())

        assert response.status_code == 200
        assert response.status_code != 429
        assert response.json() == []

    def test_seeded_progress_row_returns_non_empty_row(
        self, client, api_level
    ):
        """R002 regression at the HTTP level.

        A returning session whose ``LevelProgress`` row already exists must get
        a 200 with real data -- not a 500 and not a vacuous ``[]``.
        """
        session_key = _prime_session(client)
        LevelProgress.objects.create(
            level=api_level,
            session_key=session_key,
            best_score=820.0,
            best_resolution=1.9,
            best_run_time=24.5,
            attempts=3,
            completed=True,
        )

        response = client.get(_progress_url())

        assert response.status_code == 200
        assert response.status_code != 429

        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 1

        item = body[0]
        for field in (
            'best_score',
            'best_resolution',
            'best_run_time',
            'attempts',
            'completed',
            'level_name',
            'difficulty',
        ):
            assert field in item
        assert item['best_score'] == pytest.approx(820.0)
        assert item['best_resolution'] == pytest.approx(1.9)
        assert item['best_run_time'] == pytest.approx(24.5)
        assert item['attempts'] == 3
        assert item['completed'] is True
        assert item['level_name'] == api_level.name
        assert item['difficulty'] == api_level.difficulty

    def test_score_submission_creates_visible_progress_row(
        self, client, api_level, valid_score_payload, fresh_result_token
    ):
        """A single successful score POST alone must make progress non-empty."""
        response = client.post(
            _scores_url(),
            _with_token(
                valid_score_payload, _fresh_token(fresh_result_token)
            ),
            format='json',
        )
        assert response.status_code == 201
        assert response.status_code != 429

        progress_response = client.get(_progress_url())

        assert progress_response.status_code == 200
        assert progress_response.status_code != 429

        body = progress_response.json()
        assert len(body) == 1
        item = body[0]
        assert item['level_name'] == api_level.name
        assert item['attempts'] == 1
        assert item['completed'] is True
        assert item['best_score'] == pytest.approx(
            server_score(24.5, api_level)
        )
