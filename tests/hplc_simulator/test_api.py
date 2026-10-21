"""Endpoint-level tests for the HPLC simulator JSON API.

Covers the progress endpoint (legacy requirement API-01 / GSD requirement
R007): ``GET /hplc/api/progress/`` must return 200 with the caller's real
``LevelProgress`` rows instead of failing on a misconfigured serializer.

The progress regression deliberately creates real data (a posted score) so
it can never pass on an empty list — an assertion of "no rows" would hold
even while the endpoint 500s.

Also covers the error-hygiene contract (legacy requirement API-04 / GSD
requirement R010): no API error body may carry internal exception text.
"""

import ast
import json
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch

from hplc_simulator.models import Level, LevelProgress, UserScore


def discover_api_view_modules():
    """Return every app module that defines a DRF API view.

    The guard tests scan this set instead of a hard-coded filename so a
    newly added API view module is covered without editing the guard.
    """
    apps_root = Path(settings.BASE_DIR) / 'apps'
    modules = []
    for path in sorted(apps_root.rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        if 'APIView' in text:
            modules.append((path, text))
    return modules


def response_payload_leaks(source):
    """Names carrying exception detail into a ``Response(...)`` payload.

    Parses the module and, for every ``except ... as name:`` handler, checks
    whether the bound exception (or a variable derived from it) is referenced
    inside a ``Response(...)`` call. This catches leaks the textual ``str(e)``
    grep misses, such as ``f"{e}"``, ``repr(e)``, ``e.message_dict`` or an
    intermediate ``detail = str(e)`` variable.
    """
    tree = ast.parse(source)
    leaks = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        if handler.name is None:
            continue
        tainted = {handler.name}
        # Propagate taint through simple assignments inside the handler.
        changed = True
        while changed:
            changed = False
            for node in ast.walk(handler):
                if not isinstance(node, ast.Assign):
                    continue
                value_names = {
                    n.id
                    for n in ast.walk(node.value)
                    if isinstance(n, ast.Name)
                }
                if not value_names & tainted:
                    continue
                for target in node.targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and name.id not in tainted:
                            tainted.add(name.id)
                            changed = True
        for call in ast.walk(handler):
            if not isinstance(call, ast.Call):
                continue
            func_name = getattr(call.func, 'id', None) or getattr(
                call.func, 'attr', None
            )
            if func_name != 'Response':
                continue
            for arg in call.args:
                for name in ast.walk(arg):
                    if isinstance(name, ast.Name) and name.id in tainted:
                        leaks.append((handler.name, name.id))
    return leaks


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def sample_level(db):
    return Level.objects.create(
        name='Test Level',
        slug='test-level',
        description='A level used by the API endpoint tests',
        difficulty='beginner',
        available_columns=['C18'],
        max_pressure_bar=400.0,
        base_points=10000.0,
    )


@pytest.fixture
def valid_score_data(sample_level):
    return {
        'level_id': sample_level.id,
        'mobile_phase': {
            'start_b': 5,
            'end_b': 95,
            'ramp_time': 20,
            'ph': 3.0,
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
        'total_run_time': 25.0,
        'max_pressure_bar': 120.0,
        'min_resolution': 1.8,
        'score': 400.0,
        'is_successful': True,
        'overpressure': False,
    }


class TestLevelProgressEndpoint:
    """GET /hplc/api/progress/ — session-scoped progress rows."""

    def test_progress_endpoint_returns_level_progress(
        self, api_client, sample_level, valid_score_data
    ):
        """A returning session receives 200 and its real progress row."""
        score_response = api_client.post(
            reverse('hplc_simulator:api-scores'),
            valid_score_data,
            format='json',
        )
        assert score_response.status_code == 201

        response = api_client.get(reverse('hplc_simulator:api-progress'))

        assert response.status_code == 200
        assert len(response.data) == 1
        row = response.data[0]
        assert row['level_name'] == sample_level.name
        assert row['difficulty'] == sample_level.difficulty
        assert row['best_score'] == 400.0
        assert row['best_resolution'] == 1.8
        assert row['best_run_time'] == 25.0
        assert row['attempts'] == 1
        assert row['completed'] is True

    def test_progress_endpoint_returns_empty_list_for_new_session(
        self, api_client
    ):
        """A session with no progress gets an explicit empty result."""
        response = api_client.get(reverse('hplc_simulator:api-progress'))

        assert response.status_code == 200
        assert list(response.data) == []

    def test_progress_endpoint_excludes_other_sessions(
        self, api_client, sample_level, valid_score_data
    ):
        """Progress is scoped to the caller's session, nobody else's."""
        api_client.post(
            reverse('hplc_simulator:api-scores'),
            valid_score_data,
            format='json',
        )
        LevelProgress.objects.create(
            level=sample_level,
            session_key='some-other-session-key',
            best_score=999.0,
            attempts=7,
            completed=True,
        )

        response = api_client.get(reverse('hplc_simulator:api-progress'))

        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['best_score'] == 400.0
        assert response.data[0]['attempts'] == 1


class TestApiErrorHygiene:
    """API error bodies must never echo internal exception text."""

    def test_simulation_failure_returns_generic_error(
        self, api_client, sample_level
    ):
        """A simulator crash returns a fixed error body, not the exception."""
        payload = {
            'level_id': sample_level.id,
            'mobile_phase': {
                'start_b': 5,
                'end_b': 95,
                'ramp_time': 20,
                'ph': 3.0,
                'buffer_concentration_mm': 5.0,
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
        with patch(
            'hplc_simulator.views.generate_chromatogram',
            side_effect=RuntimeError('internal detail: solver exploded'),
        ):
            response = api_client.post(
                reverse('hplc_simulator:api-simulate'),
                payload,
                format='json',
            )

        assert response.status_code == 500
        assert response.data['error'] == 'Simulation failed'
        assert 'detail' not in response.data
        body = str(response.data).lower()
        assert 'internal detail' not in body
        assert 'solver exploded' not in body
        assert 'traceback' not in body

    def test_score_validation_failure_returns_generic_error(
        self, api_client, sample_level, valid_score_data
    ):
        """A model-level validation failure returns fixed copy only."""
        payload = dict(valid_score_data)
        # Above the level pressure limit without the overpressure flag: the
        # serializer accepts it, the model's clean() rejects it.
        payload['max_pressure_bar'] = sample_level.max_pressure_bar + 500.0
        payload['overpressure'] = False

        response = api_client.post(
            reverse('hplc_simulator:api-scores'),
            payload,
            format='json',
        )

        assert response.status_code == 400
        assert response.data['error'] == 'Invalid score submission'
        assert 'errors' not in response.data
        body = str(response.data).lower()
        assert 'exceeds level limit' not in body
        assert 'overpressure' not in body

    def test_score_persistence_failure_returns_generic_error(
        self, api_client, sample_level, valid_score_data
    ):
        """A persistence crash returns a fixed 500 body, not the exception."""
        with patch.object(
            UserScore,
            'save',
            side_effect=RuntimeError('internal detail: db write exploded'),
        ):
            response = api_client.post(
                reverse('hplc_simulator:api-scores'),
                valid_score_data,
                format='json',
            )

        assert response.status_code == 500
        assert response.data['error'] == 'Score submission failed'
        body = str(response.data).lower()
        assert 'internal detail' not in body
        assert 'db write exploded' not in body
        assert 'traceback' not in body

    def test_api_views_do_not_echo_exception_text(self):
        """Guard: no API view may put str(e) into a response body."""
        import pathlib

        import hplc_simulator.views as hplc_views

        source = pathlib.Path(hplc_views.__file__).read_text()
        assert 'str(e)' not in source
        assert 'str(exc)' not in source
        # Exception-derived payloads must not reach the response body either.
        assert 'message_dict' not in source
        assert 'exc_info=True' in source or 'logger.exception' in source


class TestApiErrorHygieneGuards:
    """Static guards that keep exception text out of every API response."""

    def test_no_api_view_module_echoes_exception_text(self):
        """No API view module may stringify an exception into a body."""
        modules = discover_api_view_modules()
        assert modules, 'expected at least one API view module to be found'

        for path, source in modules:
            assert 'str(e)' not in source, f'{path} leaks str(e)'
            assert 'str(exc)' not in source, f'{path} leaks str(exc)'
            assert 'str(err)' not in source, f'{path} leaks str(err)'
            assert 'message_dict' not in source, f'{path} leaks message_dict'

    def test_no_api_view_module_leaks_exception_into_response(self):
        """AST guard: an exception may not reach a Response() payload.

        The textual grep above only catches the literal ``str(e)`` spelling.
        This check parses each API view module and fails if a bound exception
        — directly, through an f-string, through ``repr()``, or through an
        intermediate variable — is referenced inside a ``Response(...)`` call.
        """
        modules = discover_api_view_modules()
        assert modules, 'expected at least one API view module to be found'

        for path, source in modules:
            leaks = response_payload_leaks(source)
            assert not leaks, (
                f'{path} references exception detail in a Response payload: '
                f'{leaks}'
            )

    def test_api_view_failure_paths_log_detail_server_side(self):
        """Failure detail must still reach the server log, not the client."""
        modules = discover_api_view_modules()
        assert modules, 'expected at least one API view module to be found'

        for path, source in modules:
            assert (
                'exc_info=True' in source or 'logger.exception' in source
            ), f'{path} drops the failure detail without logging it'


class TestApiUncaughtErrorHygiene:
    """An exception escaping an API view must not reach the client."""

    def test_uncaught_error_in_read_view_returns_generic_500(self, db):
        """A DB failure in a read-only view yields a generic 500 body."""
        with patch(
            'hplc_simulator.views.Level.objects.filter',
            side_effect=RuntimeError('internal detail: db down'),
        ):
            client = Client(raise_request_exception=False)
            response = client.get('/hplc/api/levels/')

        assert response.status_code == 500
        body = response.content.decode()
        assert 'internal detail' not in body
        assert 'db down' not in body
        assert 'Traceback' not in body
        assert 'RuntimeError' not in body

    def test_uncaught_error_in_write_view_returns_generic_500(
        self, sample_level, valid_score_data
    ):
        """A DB failure before the score save yields a generic 500 body."""
        with patch(
            'hplc_simulator.views.get_object_or_404',
            side_effect=RuntimeError('internal detail: lookup failed'),
        ):
            client = Client(raise_request_exception=False)
            response = client.post(
                '/hplc/api/scores/',
                data=json.dumps(valid_score_data),
                content_type='application/json',
            )

        assert response.status_code == 500
        body = response.content.decode()
        assert 'internal detail' not in body
        assert 'lookup failed' not in body
        assert 'Traceback' not in body
        assert 'RuntimeError' not in body

    def test_uncaught_error_in_simulate_view_returns_generic_500(
        self, sample_level
    ):
        """A DB failure in the simulate view yields a generic 500 body."""
        payload = {
            'level_id': sample_level.id,
            'mobile_phase': {
                'start_b': 5,
                'end_b': 95,
                'ramp_time': 20,
                'ph': 3.0,
                'buffer_concentration_mm': 5.0,
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
        with patch(
            'hplc_simulator.views.get_object_or_404',
            side_effect=RuntimeError('internal detail: level lookup failed'),
        ):
            client = Client(raise_request_exception=False)
            response = client.post(
                '/hplc/api/simulate/',
                data=json.dumps(payload),
                content_type='application/json',
            )

        assert response.status_code == 500
        body = response.content.decode()
        assert 'internal detail' not in body
        assert 'level lookup failed' not in body
        assert 'Traceback' not in body
        assert 'RuntimeError' not in body

    def test_client_errors_do_not_expose_internal_detail(self, db):
        """400/404 bodies carry only client-facing copy, never internals."""
        client = Client()

        missing_level = client.get('/hplc/api/levels/does-not-exist/')
        assert missing_level.status_code == 404
        body = missing_level.content.decode()
        assert 'Traceback' not in body
        assert 'does-not-exist' not in body

        malformed = client.post(
            '/hplc/api/simulate/',
            data='{"level_id": "not-an-int"}',
            content_type='application/json',
        )
        assert malformed.status_code == 400
        body = malformed.content.decode()
        assert 'Traceback' not in body
        assert 'internal detail' not in body
