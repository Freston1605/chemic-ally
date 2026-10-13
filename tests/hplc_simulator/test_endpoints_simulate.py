"""API endpoint coverage for the simulate route (R006, S03/T02).

Route under test:
- ``POST /hplc/api/simulate/`` (``api-simulate``)

All requests go through ``rest_framework.test.APIClient`` and
``django.urls.reverse`` -- no hardcoded URL strings -- so the tests track the
frozen URLconf instead of a copied path. Fixtures come from the shared
``tests/hplc_simulator/conftest.py`` (S03/T01) so the request shape stays
identical to what ``static/hplc_simulator/js/simulator.js`` sends.

The happy path exercises the real LSS engine end to end
(``SimulateView.post`` -> ``generate_chromatogram`` -> ``calculate_score`` ->
``SimulationResponseSerializer``). Nothing here is mocked: the response shape
is asserted exactly as the browser client consumes it.
"""

import copy

import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.fixture
def client():
    return APIClient()


def _simulate_url():
    return reverse('hplc_simulator:api-simulate')


class TestSimulateEndpointHappyPath:
    """The full real-engine response-build branch (views.py:116-161)."""

    def test_valid_payload_returns_full_simulated_response(
        self, client, valid_simulate_payload
    ):
        response = client.post(
            _simulate_url(), valid_simulate_payload, format='json'
        )

        assert response.status_code == 200
        assert response.status_code != 429

        body = response.json()

        # chromatogram: paired, equal-length numeric series from the engine.
        chromatogram = body['chromatogram']
        assert isinstance(chromatogram['time'], list)
        assert isinstance(chromatogram['signal'], list)
        assert len(chromatogram['time']) > 0
        assert len(chromatogram['signal']) == len(chromatogram['time'])

        # peaks: non-empty, real PeakInfo dicts (Ibuprofen elutes past the
        # 25 min gradient window, so 2 of the 3 analytes produce peaks).
        peaks = body['peaks']
        assert isinstance(peaks, list)
        assert len(peaks) > 0
        for peak in peaks:
            for field in (
                'analyte',
                'retention_time',
                'width',
                'height',
                'area',
            ):
                assert field in peak
            assert isinstance(peak['analyte'], str)
            assert isinstance(peak['retention_time'], (int, float))

        # metrics: the four required keys, critical_pair may be null.
        metrics = body['metrics']
        for field in (
            'total_run_time',
            'max_pressure_bar',
            'min_resolution',
            'critical_pair',
        ):
            assert field in metrics
        assert metrics['total_run_time'] > 0
        assert metrics['max_pressure_bar'] > 0
        assert (
            metrics['critical_pair'] is None
            or isinstance(metrics['critical_pair'], list)
        )

        # score/success/overpressure/message come from calculate_score().
        assert isinstance(body['score'], (int, float))
        assert body['score'] >= 0
        assert isinstance(body['success'], bool)
        assert isinstance(body['overpressure'], bool)
        assert isinstance(body['message'], str)
        assert body['message']


class TestSimulateEndpointValidationErrors:
    """Serializer and level-lookup failure paths return explicit codes."""

    def test_missing_column_key_returns_400_with_errors(
        self, client, valid_simulate_payload
    ):
        payload = copy.deepcopy(valid_simulate_payload)
        del payload['column']

        response = client.post(_simulate_url(), payload, format='json')

        assert response.status_code == 400
        assert response.status_code != 429
        assert response.status_code != 500
        assert 'errors' in response.json()

    def test_unsupported_chemistry_returns_400_with_errors(
        self, client, valid_simulate_payload
    ):
        payload = copy.deepcopy(valid_simulate_payload)
        payload['column']['chemistry'] = 'HILIC'

        response = client.post(_simulate_url(), payload, format='json')

        assert response.status_code == 400
        assert response.status_code != 429
        assert response.status_code != 500
        errors = response.json()['errors']
        # ColumnConfigSerializer rejects HILIC at the validate() level.
        assert 'column' in errors


class TestSimulateEndpointLevelLookup:
    """The ``is_active=True`` filter and unknown ids both surface as 404."""

    def test_unknown_level_id_returns_404(
        self, client, valid_simulate_payload
    ):
        payload = copy.deepcopy(valid_simulate_payload)
        payload['level_id'] = 999999

        response = client.post(_simulate_url(), payload, format='json')

        assert response.status_code == 404
        assert response.status_code != 429

    def test_inactive_level_returns_404(
        self, client, valid_simulate_payload, api_inactive_level
    ):
        payload = copy.deepcopy(valid_simulate_payload)
        payload['level_id'] = api_inactive_level.id

        response = client.post(_simulate_url(), payload, format='json')

        assert response.status_code == 404
        assert response.status_code != 429
