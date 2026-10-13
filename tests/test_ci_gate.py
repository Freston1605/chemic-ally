"""Durable guard on the scoped coverage gate wiring (R007) and pins (R020).

These tests assert the *wiring* -- that the CI test job gates on scoped
coverage and that the DRF version stays pinned -- not the runtime behavior of
the endpoints themselves (covered by test_endpoints_*).

They read tracked files (``requirements/`` and ``.github/workflows/deploy.yml``)
as plain text/regex, so no YAML dependency is introduced. ``SimpleTestCase``
blocks all DB access; these checks touch only the filesystem.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_REQUIREMENTS = REPO_ROOT / "requirements" / "development.txt"
BASE_REQUIREMENTS = REPO_ROOT / "requirements" / "base.txt"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

SCOPED_COV_MODULES = (
    "hplc_simulator.views",
    "hplc_simulator.serializers",
    "hplc_simulator.exceptions",
)


class CoverageGateWiringTests(SimpleTestCase):
    """R007: the CI test job fails below the scoped coverage threshold."""

    def test_development_requirements_pin_pytest_cov(self):
        """pytest-cov must be pinned (exact ==) so CI cannot drift."""
        text = DEVELOPMENT_REQUIREMENTS.read_text(encoding="utf-8")
        match = re.search(r"^pytest-cov==(\S+)$", text, re.MULTILINE)
        self.assertIsNotNone(
            match,
            "requirements/development.txt must pin pytest-cov with '==' "
            "so the coverage tool cannot drift in CI.",
        )

    def test_deploy_workflow_gates_on_scoped_coverage(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        threshold = re.search(r"--cov-fail-under=(\S+)", text)
        self.assertIsNotNone(
            threshold,
            ".github/workflows/deploy.yml must pass --cov-fail-under= so the "
            "test job fails below the scoped coverage threshold.",
        )
        self.assertGreater(
            int(threshold.group(1)),
            0,
            "--cov-fail-under must be a positive integer threshold.",
        )

        for module in SCOPED_COV_MODULES:
            self.assertIn(
                f"--cov={module}",
                text,
                f".github/workflows/deploy.yml must measure --cov={module} "
                "so the gate is scoped to the API surface, not the whole repo.",
            )


class DependencyPinTests(SimpleTestCase):
    """R020: DRF is pinned and must not drift."""

    def test_djangorestframework_stays_pinned(self):
        text = BASE_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn(
            "djangorestframework==3.15.2",
            text,
            "requirements/base.txt must keep djangorestframework==3.15.2 "
            "(R020 pin guard).",
        )
