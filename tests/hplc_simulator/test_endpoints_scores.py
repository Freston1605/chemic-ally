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


class TestScoreSubmissionEndpoint:
    """``POST /hplc/api/scores/`` records a score and upserts progress."""

    def test_valid_payload_records_score_and_progress(
        self, client, api_level, valid_score_payload
    ):
        session_key = _prime_session(client)

        response = client.post(
            _scores_url(), valid_score_payload, format='json'
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
        assert scores.first().score == pytest.approx(8500.0)

        progress = LevelProgress.objects.filter(
            level=api_level, session_key=session_key
        )
        assert progress.count() == 1
        record = progress.first()
        assert record.attempts == 1
        assert record.completed is True

    def test_repeat_payload_upserts_same_progress_row(
        self, client, api_level, valid_score_payload
    ):
        """Covers the ``if not created`` branch of the progress upsert."""
        session_key = _prime_session(client)

        first = client.post(_scores_url(), valid_score_payload, format='json')
        assert first.status_code == 201
        assert first.status_code != 429

        second = client.post(_scores_url(), valid_score_payload, format='json')
        assert second.status_code == 201
        assert second.status_code != 429

        progress = LevelProgress.objects.filter(
            level=api_level, session_key=session_key
        )
        # The upsert must not create a second row: unique (level, session_key).
        assert progress.count() == 1
        assert progress.first().attempts == 2

    def test_higher_score_raises_best_score(
        self, client, api_level, valid_score_payload
    ):
        session_key = _prime_session(client)

        client.post(_scores_url(), valid_score_payload, format='json')

        better = copy.deepcopy(valid_score_payload)
        better['score'] = 9200.0
        response = client.post(_scores_url(), better, format='json')

        assert response.status_code == 201
        assert response.status_code != 429

        progress = LevelProgress.objects.get(
            level=api_level, session_key=session_key
        )
        assert progress.attempts == 2
        assert progress.best_score == pytest.approx(9200.0)

    def test_overpressure_without_flag_returns_400_with_errors(
        self, client, api_level, valid_score_payload
    ):
        """Covers ScoreSubmissionView's ``full_clean`` ValidationError branch.

        ``max_pressure_bar`` is valid for the request serializer (>= 0) but
        exceeds the level limit while ``overpressure`` is False, so the model
        ``clean()`` rejects it and the view maps it to a 400 ``errors`` body.
        """
        payload = copy.deepcopy(valid_score_payload)
        payload['max_pressure_bar'] = api_level.max_pressure_bar + 500.0
        payload['overpressure'] = False

        response = client.post(_scores_url(), payload, format='json')

        assert response.status_code == 400
        assert response.status_code != 429
        assert response.status_code != 500

        errors = response.json()['errors']
        assert 'overpressure' in errors
        assert 'exceeds level limit' in str(errors['overpressure'])

    def test_missing_required_key_returns_400_with_errors(
        self, client, valid_score_payload
    ):
        payload = copy.deepcopy(valid_score_payload)
        del payload['score']

        response = client.post(_scores_url(), payload, format='json')

        assert response.status_code == 400
        assert response.status_code != 429
        assert 'errors' in response.json()

    def test_unknown_level_returns_404(self, client, valid_score_payload):
        payload = copy.deepcopy(valid_score_payload)
        payload['level_id'] = 999999

        response = client.post(_scores_url(), payload, format='json')

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
        self, client, api_level, valid_score_payload
    ):
        client.post(_scores_url(), valid_score_payload, format='json')

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
        assert item['score'] == pytest.approx(8500.0)
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
        self, client, api_level, valid_score_payload
    ):
        """A single successful score POST alone must make progress non-empty."""
        response = client.post(
            _scores_url(), valid_score_payload, format='json'
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
        assert item['best_score'] == pytest.approx(8500.0)
