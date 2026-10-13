"""Durable guards on CI/CD gate wiring (R007, R008, R020) and pins.

These tests assert the *wiring* -- that the CI test job gates on scoped
coverage, that the dependency scan is a real blocking gate, that the deploy
check runs against CI-only settings, that missing migrations fail the test
job, that the deploy job verifies itself (verify_deploy + live_probe) instead
of the old wildcard/echo escape, and that the migrator stack and calibrated
pins stay put -- not the runtime behavior of the endpoints themselves
(covered by test_endpoints_*).

They read tracked files (``requirements/``, ``.github/workflows/deploy.yml``,
``config/settings/ci.py`` and ``template.yaml``) as plain text/regex, so no
YAML dependency is introduced. ``SimpleTestCase`` blocks all DB access; these
checks touch only the filesystem.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_REQUIREMENTS = REPO_ROOT / "requirements" / "development.txt"
BASE_REQUIREMENTS = REPO_ROOT / "requirements" / "base.txt"
CI_SETTINGS = REPO_ROOT / "config" / "settings" / "ci.py"
TEMPLATE = REPO_ROOT / "template.yaml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

SCOPED_COV_MODULES = (
    "hplc_simulator.views",
    "hplc_simulator.serializers",
    "hplc_simulator.exceptions",
)

# The two djangorestframework advisories that are deliberately allowlisted
# under the R020/D015 DRF pin (PYSEC-2026-3827, PYSEC-2026-3828).
DRF_ADVISORY_IGNORES = ("PYSEC-2026-3827", "PYSEC-2026-3828")

# The single-line `run:` for the blocking pip-audit step.
PIP_AUDIT_RUN_RE = re.compile(r"^[ \t]*run: (pip-audit .*)$", re.MULTILINE)

# The single-line `run:` for the advisory safety step.
SAFETY_RUN_RE = re.compile(r"^[ \t]*run: (safety .*)$", re.MULTILINE)


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


class DependencyAuditGateTests(SimpleTestCase):
    """R008: pip-audit is a blocking dependency gate over the Lambda ship set."""

    def _pip_audit_run(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        match = PIP_AUDIT_RUN_RE.search(text)
        self.assertIsNotNone(
            match,
            ".github/workflows/deploy.yml must run a pip-audit step so the "
            "dependency scan is wired into CI (R008).",
        )
        return match.group(1)

    def test_pip_audit_step_cannot_be_swallowed(self):
        """A trailing `|| true` would make the gate non-blocking again."""
        run = self._pip_audit_run()
        self.assertNotIn(
            "|| true",
            run,
            "the pip-audit step must not be made non-blocking with '|| true' "
            "(R008); a vulnerable dependency has to fail the pipeline.",
        )

    def test_pip_audit_audits_the_lambda_ship_set(self):
        """Declaration mode audits only what the image ships, not tool deps."""
        run = self._pip_audit_run()
        self.assertIn(
            "-r requirements/requirements-lambda.txt",
            run,
            "pip-audit must audit the Lambda ship set in declaration mode "
            "(-r), otherwise it also flags the scan tools' own deps.",
        )

    def test_pip_audit_allowlist_is_exactly_the_two_drf_advisories(self):
        run = self._pip_audit_run()
        for advisory in DRF_ADVISORY_IGNORES:
            self.assertIn(
                f"--ignore-vuln {advisory}",
                run,
                f"pip-audit must allowlist {advisory} (deliberate DRF pin, "
                "R020/D015) or the blocking gate goes red for an unused code "
                "path (AdminRenderer / multipart uploads).",
            )
        self.assertEqual(
            run.count("--ignore-vuln"),
            2,
            "the pip-audit allowlist must stay minimal (only the two DRF "
            "advisories); widening it silently weakens R008.",
        )

    def test_pip_audit_tool_is_pinned(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"pip install pip-audit==\S+",
            "pip-audit must be installed at a pinned version so the blocking "
            "gate cannot shift under the workflow unnoticed.",
        )

    def test_safety_step_stays_advisory_only(self):
        """`safety check` is deprecated and exits 64; R008 is pip-audit."""
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        match = SAFETY_RUN_RE.search(text)
        self.assertIsNotNone(match, "the safety step should remain wired.")
        self.assertIn(
            "|| true",
            match.group(1),
            "the safety step must stay explicitly non-blocking (it is "
            "deprecated and exits 64); only pip-audit gates R008.",
        )


class DeploySettingsGateTests(SimpleTestCase):
    """The workflow must run Django's deploy checklist as a real gate.

    A violating production setting (e.g. DEBUG=True, missing HTTPS config)
    has to fail the pipeline via `check --deploy --fail-level WARNING`,
    executed under the CI-only settings module (dummy DB backend).
    """

    def test_workflow_runs_deploy_check_under_ci_settings(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "DJANGO_SETTINGS_MODULE: config.settings.ci",
            text,
            ".github/workflows/deploy.yml must run the deploy check under "
            "config.settings.ci (not development or production) so the CI "
            "job can load without psycopg2/RDS.",
        )
        self.assertIn(
            "python manage.py check --deploy --fail-level WARNING",
            text,
            ".github/workflows/deploy.yml must run 'check --deploy "
            "--fail-level WARNING' so a production deploy-settings "
            "violation fails the pipeline.",
        )

    def test_ci_settings_module_exists_with_dummy_backend(self):
        self.assertTrue(
            CI_SETTINGS.is_file(),
            "config/settings/ci.py must exist: the deploy-settings gate "
            "imports config.settings.ci.",
        )
        text = CI_SETTINGS.read_text(encoding="utf-8")
        self.assertIn(
            "django.db.backends.dummy",
            text,
            "config/settings/ci.py must use django.db.backends.dummy so the "
            "CI test job (development requirements, no psycopg2) can run "
            "check --deploy and makemigrations --check without a database.",
        )


class MigrationGateTests(SimpleTestCase):
    """A missing migration must fail the CI test job, not only prod boot."""

    def test_test_job_gates_on_missing_migrations(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"python manage\.py makemigrations --check",
            ".github/workflows/deploy.yml must run 'makemigrations --check' "
            "so a model change without a migration fails the pipeline.",
        )


class DeployVerificationGateTests(SimpleTestCase):
    """The deploy job must verify itself and lose its silent-success escape."""

    def test_deploy_job_runs_verify_deploy(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "python scripts/verify_deploy.py",
            text,
            ".github/workflows/deploy.yml must reference "
            "scripts/verify_deploy.py so the deployed LastUpdateStatus is "
            "checked after the SAM deploy.",
        )

    def test_deploy_job_runs_live_probe(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "python scripts/live_probe.py",
            text,
            ".github/workflows/deploy.yml must reference scripts/live_probe.py "
            "so a deploy that never serves a live 200 fails the pipeline.",
        )

    def test_deploy_job_no_longer_waits_for_wildcard(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn(
            "chemically-DjangoFunction-*",
            text,
            "the deploy job must not fall back to a wildcard "
            "chemically-DjangoFunction-* lookup; verification goes through "
            "scripts/verify_deploy.py.",
        )

    def test_deploy_job_has_no_still_updating_echo_escape(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn(
            '|| echo "Function still updating',
            text,
            "the deploy job must not swallow an incomplete stack update with "
            "'|| echo \"Function still updating...'; an unfinished update "
            "has to fail scripts/verify_deploy.py and the pipeline.",
        )


class MigratorAndCalibrationPinsTests(SimpleTestCase):
    """The migrator stack and the pip-audit calibration pins must stay put."""

    def test_template_declares_migrate_function(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "MigrateFunction:",
            text,
            "template.yaml must declare the MigrateFunction resource so "
            "migrations ship with the stack.",
        )
        self.assertIn(
            "config.migrate.handler",
            text,
            "template.yaml must point MigrateFunction at the "
            "config.migrate.handler command (migrate --noinput + "
            "createcachetable).",
        )

    def test_django_pin_matches_pip_audit_calibration(self):
        text = BASE_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn(
            "Django==5.2.18",
            text,
            "requirements/base.txt must pin Django==5.2.18 (the pip-audit "
            "calibration): drift re-opens advisories the allowlist does not "
            "cover and turns the blocking scan red.",
        )

    def test_python_dotenv_pin_matches_pip_audit_calibration(self):
        text = BASE_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn(
            "python-dotenv==1.2.4",
            text,
            "requirements/base.txt must pin python-dotenv==1.2.4 (the "
            "pip-audit calibration): drift re-opens advisories the "
            "allowlist does not cover and turns the blocking scan red.",
        )
