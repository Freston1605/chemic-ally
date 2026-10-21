"""Production throttle-rate knob: valid override applies, malformed fails fast.

M006/S02 made the DRF scoped-throttle budgets overridable in the deployed
environment (``THROTTLE_RATE_SIMULATE`` / ``THROTTLE_RATE_SCORES`` in
``config/settings/production.py``). A malformed value must be rejected when
the settings module is imported -- DRF parses the rate lazily, at request
time, so a typo would otherwise surface as a 500 on the first throttled
request instead of a failed deploy.

Each case boots the real production settings module in a subprocess with an
explicitly built, hermetic environment (same pattern as
``tests/test_settings_matrix.py``): the parent's environment minus
``SECRET_KEY``, plus the RDS_*/AWS_STORAGE_BUCKET_NAME variables production
requires, and ``DOTENV_PATH=""`` so a stray ``.env`` cannot supply a value.

``SimpleTestCase`` is used deliberately: these checks never touch the
database.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent

DUMMY_PRODUCTION_SECRET_KEY = (
    "probe-only-insecure-key-0123456789-abcdefghij-ABCDEFGHIJ-xyz"
)

DUMMY_PRODUCTION_ENV = {
    "SECRET_KEY": DUMMY_PRODUCTION_SECRET_KEY,
    "RDS_DB_NAME": "probe",
    "RDS_USERNAME": "probe",
    "RDS_PASSWORD": "probe",
    "RDS_HOSTNAME": "localhost",
    "RDS_PORT": "5432",
    "AWS_STORAGE_BUCKET_NAME": "probe-bucket",
}

# Prints the effective throttle rates of the booted production module.
RATE_SNIPPET = (
    "import django; django.setup(); "
    "from django.conf import settings; "
    "import json; "
    "print(json.dumps(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']))"
)


def _child_env(extra_env=None):
    """Build the explicit, hermetic environment for one production probe."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "SECRET_KEY"
    }
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
    env["PATH"] = os.environ.get("PATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["DOTENV_PATH"] = ""
    for noisy in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION"):
        env.pop(noisy, None)
    env.update(DUMMY_PRODUCTION_ENV)
    if extra_env:
        env.update(extra_env)
    return env


def _rate_probe(extra_env=None):
    """Boot production settings in a subprocess and print the throttle rates."""
    return subprocess.run(
        [sys.executable, "-c", RATE_SNIPPET],
        cwd=REPO_ROOT,
        env=_child_env(extra_env),
        capture_output=True,
        text=True,
    )


class ProductionThrottleRateTests(SimpleTestCase):
    """The rate knob is honoured when valid and rejected when malformed."""

    def test_default_rates_are_the_base_defaults(self):
        result = _rate_probe()
        self.assertEqual(
            result.returncode,
            0,
            "production settings could not boot with the documented "
            "environment; output:\n" + (result.stderr or ""),
        )
        rates = json.loads(result.stdout.strip())
        self.assertEqual(rates["simulate"], "60/min")
        self.assertEqual(rates["scores"], "30/min")

    def test_valid_overrides_are_applied(self):
        result = _rate_probe({
            "THROTTLE_RATE_SIMULATE": "120/min",
            "THROTTLE_RATE_SCORES": "5/min",
        })
        self.assertEqual(
            result.returncode,
            0,
            "a valid throttle-rate override broke the production boot; "
            "output:\n" + (result.stderr or ""),
        )
        rates = json.loads(result.stdout.strip())
        self.assertEqual(rates["simulate"], "120/min")
        self.assertEqual(rates["scores"], "5/min")

    def test_malformed_simulate_rate_fails_the_boot(self):
        result = _rate_probe({"THROTTLE_RATE_SIMULATE": "fast"})
        self.assertNotEqual(
            result.returncode,
            0,
            "a malformed THROTTLE_RATE_SIMULATE booted production; DRF "
            "parses rates lazily, so the failure would surface as a 500 on "
            "the first throttled request instead of a failed deploy.",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertIn("ImproperlyConfigured", combined)
        self.assertIn("THROTTLE_RATE_SIMULATE", combined)

    def test_malformed_scores_rate_fails_the_boot(self):
        result = _rate_probe({"THROTTLE_RATE_SCORES": "30/xyz"})
        self.assertNotEqual(
            result.returncode,
            0,
            "a malformed THROTTLE_RATE_SCORES booted production; the rate "
            "must be rejected at import time.",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertIn("ImproperlyConfigured", combined)
        self.assertIn("THROTTLE_RATE_SCORES", combined)

    def test_empty_rate_fails_the_boot(self):
        """An empty override is a misconfiguration, not a free pass."""
        result = _rate_probe({"THROTTLE_RATE_SCORES": ""})
        self.assertNotEqual(
            result.returncode,
            0,
            "an empty THROTTLE_RATE_SCORES booted production.",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertIn("THROTTLE_RATE_SCORES", combined)
