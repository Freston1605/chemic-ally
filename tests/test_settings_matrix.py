"""Settings matrix: every module a human or pipeline can boot, proven by subprocess.

S05-T01 removed the hard-coded ``SECRET_KEY`` fallback from ``base.py``. The
fail-fast half of R016 is proven by ``tests/test_secret_key_failfast.py``;
this module proves the OTHER half -- that removing the fallback broke no real
boot path. Each case spawns a real Python subprocess that boots Django and
prints ``settings.SECRET_KEY``, so a module that cannot boot is a genuine
failure rather than an importlib cache artifact:

* ``config.settings.development`` with SECRET_KEY scrubbed -> exit 0 (the
  module seeds its own local-only placeholder before importing base).
* ``config.settings.testing`` -> exit 0 (pytest-only escape hatch; it seeds
  the key before importing development/base, exactly like ci.py).
* ``config.settings.ci`` with SECRET_KEY scrubbed -> exit 0 (ci.py seeds its
  own key before importing production -- the case a naive base.py change
  breaks, because the production import happens inside the same module load).
* ``config.settings.production`` with SECRET_KEY scrubbed -> non-zero exit,
  with SECRET_KEY named in the output.
* ``config.settings.production`` with a long dummy key plus the RDS_* and
  AWS_STORAGE_BUCKET_NAME variables set -> exit 0 (a correctly configured
  production environment still boots; the strict base must not have made the
  module unbootable for real deployments).
* ``config.settings.base`` with SECRET_KEY scrubbed -> non-zero exit naming
  ImproperlyConfigured (the settings-level fail-fast proof for R016,
  complementing the manage.py-level test from S05-T01).

The child environment is built explicitly -- the parent environment minus
SECRET_KEY, plus PYTHONPATH/PATH, with DOTENV_PATH="" so base.py's
``load_dotenv`` cannot read a stray ``.env`` from an ancestor directory --
so a developer's shell exports or pytest-env injection cannot make a case
pass for the wrong reason.

``SimpleTestCase`` is used deliberately: none of these checks touch the
database.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent

# Deliberately long and obviously fake: production's security.W009 heuristic
# needs >=50 characters with >=5 unique characters, and this probe must prove
# a *well-formed* production environment boots, not a degenerate one.
DUMMY_PRODUCTION_SECRET_KEY = (
    "probe-only-insecure-key-0123456789-abcdefghij-ABCDEFGHIJ-xyz"
)

# The full set of environment variables production.py requires beyond
# SECRET_KEY (see config/settings/production.py _required_env).
DUMMY_PRODUCTION_ENV = {
    "RDS_DB_NAME": "probe",
    "RDS_USERNAME": "probe",
    "RDS_PASSWORD": "probe",
    "RDS_HOSTNAME": "localhost",
    "RDS_PORT": "5432",
    "AWS_STORAGE_BUCKET_NAME": "probe-bucket",
}

# Boots Django under the chosen settings module and prints "OK" iff
# settings.SECRET_KEY is truthy. A settings module that cannot even be
# imported makes this exit non-zero with a traceback on stderr.
BOOT_SNIPPET = (
    "import django; django.setup(); "
    "from django.conf import settings; "
    'print(settings.SECRET_KEY and "OK")'
)


def _child_env(settings_module, extra_env=None):
    """Build the explicit, hermetic environment for one boot probe."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "SECRET_KEY"
    }
    # The settings module is set explicitly so an inherited
    # DJANGO_SETTINGS_MODULE from the pytest process cannot leak into the
    # child and boot the wrong module.
    env["DJANGO_SETTINGS_MODULE"] = settings_module
    env["PATH"] = os.environ.get("PATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT)
    # DOTENV_PATH="" disables base.py's .env load entirely, so a stray .env
    # in this checkout or an ancestor directory cannot supply SECRET_KEY and
    # turn a behavioral probe into a false pass.
    env["DOTENV_PATH"] = ""
    for noisy in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION"):
        env.pop(noisy, None)
    if extra_env:
        env.update(extra_env)
    return env


def _boot_probe(settings_module, extra_env=None):
    """Boot Django in a subprocess under *settings_module*."""
    return subprocess.run(
        [sys.executable, "-c", BOOT_SNIPPET],
        cwd=REPO_ROOT,
        env=_child_env(settings_module, extra_env),
        capture_output=True,
        text=True,
    )


class DevelopmentBootTests(SimpleTestCase):
    """Local dev must boot with no ambient SECRET_KEY (S05-T01 risk case)."""

    def test_development_boots_with_secret_key_scrubbed(self):
        result = _boot_probe("config.settings.development")
        self.assertEqual(
            result.returncode,
            0,
            "config.settings.development could not boot without an ambient "
            "SECRET_KEY; it must seed a local placeholder before importing "
            "base. Output:\n" + (result.stderr or ""),
        )
        self.assertEqual(
            result.stdout.strip(),
            "OK",
            "development settings loaded but SECRET_KEY is falsy.",
        )


class TestingBootTests(SimpleTestCase):
    """The pytest-only settings module must stand alone with no ambient key."""

    def test_testing_settings_boot(self):
        result = _boot_probe("config.settings.testing")
        self.assertEqual(
            result.returncode,
            0,
            "config.settings.testing could not boot without an ambient "
            "SECRET_KEY. Output:\n" + (result.stderr or ""),
        )
        self.assertEqual(
            result.stdout.strip(),
            "OK",
            "testing settings loaded but SECRET_KEY is falsy.",
        )


class CiBootTests(SimpleTestCase):
    """The CI gate module must boot with SECRET_KEY scrubbed from the env.

    ci.py seeds its own key via os.environ.setdefault BEFORE importing
    production; deleting that seed (or reordering it below the import) is
    exactly the regression this case exists to catch, because the production
    import happens during the same module load.
    """

    def test_ci_boots_with_secret_key_scrubbed(self):
        result = _boot_probe("config.settings.ci")
        self.assertEqual(
            result.returncode,
            0,
            "config.settings.ci could not boot without an ambient SECRET_KEY; "
            "it must seed its own key before importing production. Output:\n"
            + (result.stderr or ""),
        )
        self.assertEqual(
            result.stdout.strip(),
            "OK",
            "ci settings loaded but SECRET_KEY is falsy.",
        )


class ProductionBootTests(SimpleTestCase):
    """Production refuses to boot unkeyed and boots when properly keyed."""

    def test_production_refuses_to_boot_with_secret_key_scrubbed(self):
        result = _boot_probe("config.settings.production")
        self.assertNotEqual(
            result.returncode,
            0,
            "config.settings.production booted without SECRET_KEY -- the "
            "fail-fast guard in base.py is not in effect for the deployed "
            "module (R016).",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertIn(
            "SECRET_KEY",
            combined,
            "the production boot failure must name SECRET_KEY; got:\n"
            + combined,
        )

    def test_production_boots_with_key_and_required_env(self):
        result = _boot_probe(
            "config.settings.production",
            dict(DUMMY_PRODUCTION_ENV, SECRET_KEY=DUMMY_PRODUCTION_SECRET_KEY),
        )
        self.assertEqual(
            result.returncode,
            0,
            "a correctly configured production environment (SECRET_KEY plus "
            "the RDS_*/AWS_STORAGE_BUCKET_NAME variables) could not boot; "
            "the stricter base.py must not have made the deployed module "
            "unbootable. Output:\n" + (result.stderr or ""),
        )
        self.assertEqual(
            result.stdout.strip(),
            "OK",
            "production settings loaded but SECRET_KEY is falsy.",
        )


class BaseFailFastTests(SimpleTestCase):
    """base.py itself must refuse to import with SECRET_KEY unset (R016)."""

    def test_base_refuses_to_boot_with_secret_key_scrubbed(self):
        result = _boot_probe("config.settings.base")
        self.assertNotEqual(
            result.returncode,
            0,
            "config.settings.base imported successfully without SECRET_KEY; "
            "it must raise ImproperlyConfigured (R016 fail-fast proof at the "
            "settings level).",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertIn(
            "ImproperlyConfigured",
            combined,
            "expected ImproperlyConfigured in the boot failure output; "
            "got:\n" + combined,
        )
