"""Non-leaking API error handling tests (R005, D006).

Covers:
- the direct ``api_exception_handler`` contract for connection-level
  database failures and for errors that must *not* be swallowed;
- wiring proof that ``REST_FRAMEWORK['EXCEPTION_HANDLER']`` is active so a
  DB-backed endpoint returns an explicit 503 instead of an unhandled 500;
- R005: ``SimulateView`` no longer leaks internal exception text.
"""

from unittest.mock import MagicMock, patch

from django.db import InterfaceError, OperationalError, ProgrammingError
from django.urls import reverse
from rest_framework.test import APIClient

from hplc_simulator.exceptions import DB_UNAVAILABLE_ERROR, api_exception_handler


def _valid_simulate_payload():
    return {
        "level_id": 1,
        "mobile_phase": {
            "start_b": 5,
            "end_b": 95,
            "ramp_time": 20,
            "ph": 3.0,
            "buffer_concentration_mm": 25,
            "is_gradient": True,
        },
        "column": {
            "chemistry": "C18",
            "length_mm": 150,
            "id_mm": 4.6,
            "particle_size_um": 5.0,
        },
        "operation": {
            "flow_rate_ml_min": 1.0,
            "temperature_c": 30,
            "injection_volume_ul": 10,
            "dwell_time_min": 1.0,
        },
    }


class TestExceptionHandlerDirect:
    """Unit contract for the DRF exception handler."""

    def test_operational_error_becomes_non_leaking_503(self):
        response = api_exception_handler(OperationalError("boom"), {})
        assert response is not None
        assert response.status_code == 503
        assert response.data == {"error": DB_UNAVAILABLE_ERROR}
        assert "boom" not in str(response.data)

    def test_interface_error_becomes_non_leaking_503(self):
        response = api_exception_handler(InterfaceError("socket lost"), {})
        assert response is not None
        assert response.status_code == 503
        assert response.data == {"error": DB_UNAVAILABLE_ERROR}
        assert "socket lost" not in str(response.data)

    def test_programming_error_is_not_swallowed(self):
        """Non-connection DB errors keep DRF's default (unhandled) path."""
        response = api_exception_handler(ProgrammingError("bad sql"), {})
        assert response is None


class TestDatabaseUnavailableWiring:
    """Proves EXCEPTION_HANDLER is wired into the live request path."""

    def test_level_list_returns_503_when_db_unreachable(self):
        client = APIClient()
        with patch("hplc_simulator.views.Level.objects") as mock_objects:
            mock_objects.filter.side_effect = OperationalError("boom")
            response = client.get(reverse("hplc_simulator:api-levels"))

        assert response.status_code == 503
        body = response.json()
        assert "error" in body
        assert "boom" not in response.content.decode()
        assert "Traceback" not in response.content.decode()


class TestSimulateViewDoesNotLeak:
    """R005: the simulate endpoint must not echo internal exception text."""

    def test_simulation_failure_returns_generic_500(self):
        level = MagicMock()
        level.max_pressure_bar = 400.0
        level.analytes.all.return_value = []

        client = APIClient()
        with patch(
            "hplc_simulator.views.get_object_or_404", return_value=level
        ), patch(
            "hplc_simulator.views.generate_chromatogram",
            side_effect=RuntimeError("secret-detail"),
        ):
            response = client.post(
                reverse("hplc_simulator:api-simulate"),
                _valid_simulate_payload(),
                format="json",
            )

        assert response.status_code == 500
        body = response.json()
        assert body == {"error": "Simulation failed"}
        assert "detail" not in body
        assert "secret-detail" not in response.content.decode()
