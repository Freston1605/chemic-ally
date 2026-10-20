"""R016: SECRET_KEY is mandatory and has no committed fallback.

Three independent layers of proof, because a single one is easy to fake:

1. ``SecretKeySourceTests`` — source-level assertions that the removed literal
   fallback is really gone and that ``base.py`` raises ``ImproperlyConfigured``
   instead of defaulting.
2. ``SecretKeyFailFastSubprocessTests`` — a real ``manage.py check`` process
   with ``SECRET_KEY`` scrubbed from the environment. This is the behavioral
   proof: it fails red if the fallback is restored, and it cannot be satisfied
   by a pytest-env value because the subprocess never loads that plugin.
3. ``SecretKeyUsableTests`` — an in-process check that the key the suite runs
   under is present and can actually sign/unsign, so the fail-fast change did
   not simply break signing.

``SimpleTestCase`` is used deliberately: none of these checks touch the
database.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.signing import TimestampSigner
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_SETTINGS = REPO_ROOT / "config" / "settings" / "base.py"

# The exact literal that used to be the committed fallback.
OLD_FALLBACK = "hg&fx+3xp@0sf2s^#(hi#tqrbim!q473umn#k+i!ov)55dv5v*"


class SecretKeySourceTests(SimpleTestCase):
    """The committed fallback must be gone from the working tree."""

    def setUp(self):
        self.source = BASE_SETTINGS.read_text(encoding="utf-8")

    def test_literal_fallback_is_absent(self):
        self.assertNotIn(
            OLD_FALLBACK,
            self.source,
            "config/settings/base.py still contains the committed SECRET_KEY "
            "fallback (R016).",
        )

    def test_base_raises_improperly_configured_when_unset(self):
        self.assertIn("ImproperlyConfigured", self.source)
        self.assertIn(
            "SECRET_KEY environment variable must be set",
            self.source,
        )

    def test_base_imports_improperly_configured(self):
        self.assertIn(
            "from django.core.exceptions import ImproperlyConfigured",
            self.source,
        )


class SecretKeyFailFastSubprocessTests(SimpleTestCase):
    """A real process with no SECRET_KEY must refuse to start.

    The scrubbed environment is the whole point: ``pytest.ini`` injects
    ``SECRET_KEY`` through pytest-env, but that plugin lives inside the pytest
    process, so a child ``manage.py`` invocation never sees it. If ``base.py``
    regains a default, this test goes red.
    """

    def _run_manage_check_without_secret_key(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "SECRET_KEY"
        }
        # Keep the run hermetic and self-describing: no ambient settings
        # module, no inherited PYTHONPATH surprises beyond the repo root.
        #
        # S05-T04: this probes ``config.settings.base`` directly, NOT
        # ``development``. R016's fail-fast contract belongs to base (and to
        # production), while ``development`` is explicitly required to seed its
        # own local-only placeholder so a bare ``manage.py`` command keeps
        # working in a checkout with no ``.env`` (slice plan S05-T05 case:
        # "config.settings.development with SECRET_KEY removed -> exit 0").
        # Pointing this at ``development`` made the test depend on local dev
        # being broken, which contradicts that contract.
        env["DJANGO_SETTINGS_MODULE"] = "config.settings.base"
        env["PATH"] = os.environ.get("PATH", "")
        env["PYTHONPATH"] = str(REPO_ROOT)
        # base.py loads .env only from an explicit path. Disable that entirely
        # (DOTENV_PATH="") so an ancestor .env outside this checkout cannot
        # supply SECRET_KEY and turn this behavioral proof into a false pass.
        env["DOTENV_PATH"] = ""
        for noisy in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION"):
            env.pop(noisy, None)
        return subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_manage_check_fails_without_secret_key(self):
        """base (and therefore production) must refuse to start unkeyed."""
        result = self._run_manage_check_without_secret_key()
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertNotEqual(
            result.returncode,
            0,
            "manage.py check succeeded without SECRET_KEY — the fail-fast "
            "guard in base.py is not in effect (R016).",
        )
        self.assertIn(
            "ImproperlyConfigured",
            combined,
            "Expected ImproperlyConfigured in the failure output; got:\n"
            + combined,
        )
        self.assertIn("SECRET_KEY environment variable must be set", combined)

    def test_development_seeds_its_own_key_without_ambient_secret(self):
        """Local dev must still boot with no ambient SECRET_KEY (S05-T05).

        The mirror image of the fail-fast case: ``development.py`` seeds an
        obviously-fake placeholder *before* importing ``base``, so a
        developer's bare ``manage.py runserver``/``migrate`` works from a fresh
        checkout. If that pre-seed is moved back below the ``from .base import
        *`` line, the import aborts first and this test goes red.
        """
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "SECRET_KEY"
        }
        env["DJANGO_SETTINGS_MODULE"] = "config.settings.development"
        env["PATH"] = os.environ.get("PATH", "")
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["DOTENV_PATH"] = ""
        result = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            "config.settings.development could not boot without an ambient "
            "SECRET_KEY; it must seed a local placeholder before importing "
            "base. Output:\n" + (result.stderr or ""),
        )


class TestingSettingsModuleTests(SimpleTestCase):
    """config.settings.testing must stand alone with no ambient SECRET_KEY.

    It imports development, which imports base, which raises
    ImproperlyConfigured when the key is unset. The module therefore has to
    seed the key *before* that import chain runs, exactly like ci.py does.
    """

    def _import_testing_module_without_secret_key(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "SECRET_KEY"
        }
        env["DJANGO_SETTINGS_MODULE"] = "config.settings.testing"
        env["PATH"] = os.environ.get("PATH", "")
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["DOTENV_PATH"] = ""
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from django.conf import settings; "
                "print(settings.SECRET_KEY)",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_testing_settings_import_without_ambient_key(self):
        result = self._import_testing_module_without_secret_key()
        self.assertEqual(
            result.returncode,
            0,
            "config.settings.testing could not load without an ambient "
            "SECRET_KEY. It must seed its own key before importing "
            "development/base. Output:\n" + (result.stderr or ""),
        )
        self.assertEqual(
            result.stdout.strip(),
            "test-only-insecure-key-0123456789-abcdefghij",
        )

    def test_testing_settings_module_is_never_production(self):
        # Guards against someone wiring this pytest-only module into a
        # deployed settings path.
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
            encoding="utf-8"
        )
        template = (REPO_ROOT / "template.yaml").read_text(encoding="utf-8")
        self.assertNotIn("config.settings.testing", workflow)
        self.assertNotIn("config.settings.testing", template)


class SecretKeyUsableTests(SimpleTestCase):
    """The suite's key is present and genuinely usable for signing."""

    def test_secret_key_is_set(self):
        self.assertTrue(
            settings.SECRET_KEY,
            "SECRET_KEY is empty under the test settings.",
        )

    def test_secret_key_is_not_the_old_committed_value(self):
        self.assertNotEqual(settings.SECRET_KEY, OLD_FALLBACK)

    def test_timestamp_signer_round_trips(self):
        signer = TimestampSigner()
        signed = signer.sign("result_token-probe")
        self.assertEqual(
            signer.unsign(signed, max_age=60),
            "result_token-probe",
        )
