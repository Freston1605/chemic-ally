"""DB-less production regression tests (D006).

Production runs without a database (no RDS instance is provisioned). The
calculators are intentionally DB-less, but the default database-backed
session engine makes a calculator POST fail inside
``SessionMiddleware.process_response`` — outside the view's ``try``/``except``
— which surfaces as an HTTP 500. These tests pin:

- the production session engine is the signed-cookie backend, so no request
  ever needs a database;
- ``add_previous_substances`` swallows connection-level session-store
  failures instead of failing the calculation;
- valid calculator POSTs (reaction balancer and equilibria) return 200 and
  render their result with *no* database reachable;
- the DB-backed ``/hplc/`` endpoints keep their explicit, non-leaking 503.
"""

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.db import InterfaceError, OperationalError, ProgrammingError
from django.test import Client, SimpleTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from chemistry_calculators.utils import add_previous_substances
from hplc_simulator.exceptions import DB_UNAVAILABLE_ERROR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings" / "production.py"

_DEFAULT_SESSION_ENGINE = "django.contrib.sessions.backends.db"
_SIGNED_COOKIE_ENGINE = "django.contrib.sessions.backends.signed_cookies"

_PRODUCTION_ENV = {
    "SECRET_KEY": "test-secret-key-for-dbless-production",
    "RDS_DB_NAME": "chemically",
    "RDS_USERNAME": "chemically",
    "RDS_PASSWORD": "rds-secret",
    "RDS_HOSTNAME": "db.invalid",
    "RDS_PORT": "5432",
    "AWS_STORAGE_BUCKET_NAME": "chemically-env",
}


def _load_production_settings():
    """Execute production.py with the required env vars present.

    Loaded as a throwaway module (never inserted into ``sys.modules``) so the
    active test settings are untouched.
    """
    from config.settings import base as base_settings

    allowed_hosts_before = list(base_settings.ALLOWED_HOSTS)
    with patch.dict(os.environ, _PRODUCTION_ENV):
        spec = importlib.util.spec_from_file_location(
            "config.settings.production_under_test", PRODUCTION_SETTINGS_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    # production.py does ``ALLOWED_HOSTS += [...]`` on the list exported by
    # base; restore it so loading stays side-effect free for other tests.
    base_settings.ALLOWED_HOSTS[:] = allowed_hosts_before
    return module


def _production_session_engine():
    """The session engine production actually configures.

    Falls back to Django's default DB-backed engine when production.py does
    not set one, so the behavioural test fails while the defect exists.
    """
    return getattr(
        _load_production_settings(), "SESSION_ENGINE", _DEFAULT_SESSION_ENGINE
    )


def _valid_reaction_data():
    return {"reactant": "H2 O2", "product": "H2O", "reversible": True}


def _valid_equilibria_data():
    return {
        "reactions": json.dumps([
            {
                "reactants": "HCO3-",
                "products": "H+ + CO3-2",
                "k_mode": "pKa",
                "k_value": "10.3",
            },
            {
                "reactants": "H2CO3",
                "products": "H+ + HCO3-",
                "k_mode": "pKa",
                "k_value": "6.3",
            },
            {
                "reactants": "H2O",
                "products": "H+ + OH-",
                "k_mode": "pKa",
                "k_value": "14.0",
            },
        ]),
        "concentrations": '{"HCO3-": {"value": 0.01, "unit": "mol/L"}}',
        "solvent": "H2O",
        "solvent_concentration": 55.4,
    }


class ProductionSessionConfigurationTests(SimpleTestCase):
    """The DB-absent production contract lives in the production settings."""

    def test_session_engine_is_database_free(self):
        self.assertEqual(
            _production_session_engine(), _SIGNED_COOKIE_ENGINE
        )

    def test_security_settings_are_not_weakened(self):
        settings_module = _load_production_settings()
        self.assertTrue(settings_module.SESSION_COOKIE_SECURE)
        self.assertTrue(settings_module.CSRF_COOKIE_SECURE)
        self.assertFalse(
            getattr(settings_module, "CSRF_USE_SESSIONS", False)
        )


class AddPreviousSubstancesTests(SimpleTestCase):
    """Defence in depth: a dead session store must not fail the calculation."""

    def _request(self, session):
        request = MagicMock()
        request.session = session
        return request

    def test_read_connection_error_is_swallowed_and_logged(self):
        session = MagicMock()
        session.get.side_effect = OperationalError("db is gone")
        with self.assertLogs("chemistry_calculators.utils", level="ERROR"):
            add_previous_substances(self._request(session), ["H2O"])
        session.__setitem__.assert_not_called()

    def test_write_connection_error_is_swallowed_and_logged(self):
        session = MagicMock()
        session.get.return_value = []
        session.__setitem__.side_effect = OperationalError("db is gone")
        with self.assertLogs("chemistry_calculators.utils", level="ERROR"):
            add_previous_substances(self._request(session), ["H2O"])

    def test_interface_error_is_swallowed_and_logged(self):
        session = MagicMock()
        session.get.side_effect = InterfaceError("socket lost")
        with self.assertLogs("chemistry_calculators.utils", level="ERROR"):
            add_previous_substances(self._request(session), ["H2O"])

    def test_programming_error_is_not_swallowed(self):
        session = MagicMock()
        session.get.side_effect = ProgrammingError("bad sql")
        with self.assertRaises(ProgrammingError):
            add_previous_substances(self._request(session), ["H2O"])

    def test_success_path_still_persists(self):
        session = {}
        add_previous_substances(self._request(session), ["H2O", "CO2"])
        assert session["previous_substances"] == ["H2O", "CO2"]


class DblessCalculatorTests(SimpleTestCase):
    """The calculator happy paths must work with no database reachable.

    ``SimpleTestCase`` blocks every database query, so any DB access fails the
    test instead of being masked. The session engine used is the one
    production actually configures, so the test fails while the defect exists.
    """

    def setUp(self):
        self.client = Client()
        self.engine = _production_session_engine()

    def test_valid_reaction_post_renders_result_without_db(self):
        with self.settings(SESSION_ENGINE=self.engine):
            response = self.client.post(
                reverse("reaction_balancer"), _valid_reaction_data()
            )
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertTrue(result["success"])
        self.assertIn("\\ce", result["latex"])
        self.assertIn(b'id="reaction-result"', response.content)

    def test_impossible_reaction_post_still_200_without_db(self):
        data = {
            "reactant": "notAFormula",
            "product": "H2O",
            "reversible": True,
        }
        with self.settings(SESSION_ENGINE=self.engine):
            response = self.client.post(reverse("reaction_balancer"), data)
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertFalse(result["success"])
        self.assertTrue(result["error"])
        content = response.content.decode()
        self.assertIn("Calculation Failed", content)
        self.assertNotIn("Traceback", content)

    def test_one_sided_reaction_post_still_200_without_db(self):
        data = {"reactant": "H2 O2", "product": "", "reversible": True}
        with self.settings(SESSION_ENGINE=self.engine):
            response = self.client.post(reverse("reaction_balancer"), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Provide both reactants and products.",
            response.content.decode(),
        )

    def test_valid_equilibria_post_200_without_db(self):
        with self.settings(SESSION_ENGINE=self.engine):
            response = self.client.post(
                reverse("equilibria"), _valid_equilibria_data()
            )
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertIsNotNone(result)
        self.assertTrue(result.get("success"))


class HplcBehaviourUnchangedTests(SimpleTestCase):
    """The DB-backed /hplc/ endpoints keep their explicit non-leaking 503."""

    def test_simulator_index_still_returns_200(self):
        response = self.client.get("/hplc/")
        self.assertEqual(response.status_code, 200)

    def test_level_list_returns_non_leaking_503(self):
        client = APIClient()
        with patch("hplc_simulator.views.Level.objects") as mock_objects:
            mock_objects.filter.side_effect = OperationalError(
                "could not connect to server: rds-host"
            )
            response = client.get("/hplc/api/levels/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": DB_UNAVAILABLE_ERROR})
        body = response.content.decode()
        for leaked in (
            "Traceback",
            "OperationalError",
            "InterfaceError",
            "rds-host",
            "could not connect",
        ):
            self.assertNotIn(leaked, body)
