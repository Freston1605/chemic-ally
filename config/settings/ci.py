"""CI-only Django settings.

This module exists solely for the GitHub Actions ``test`` job. It reuses the
production security posture (``config.settings.production``) so that
``manage.py check --deploy --fail-level WARNING`` genuinely gates on Django's
deployment checklist, but it swaps the PostgreSQL database for Django's
``dummy`` backend because the CI job has no RDS instance to connect to (the
driver itself ships via ``requirements/development.txt`` -- S05-T06's settings
matrix boots the production module in a subprocess, which imports it).

**This module is CI-only and is never deployed.** The Lambda container sets
``DJANGO_SETTINGS_MODULE=config.settings.production`` (see ``template.yaml``);
only the CI workflow imports this file.

The ``os.environ.setdefault`` calls below MUST run before the production
import. ``base.py`` calls ``load_dotenv(override=False)``, so a value already
present in ``os.environ`` wins over ``.env.example`` — without these defaults a
checked-out ``.env`` would supply ``SECRET_KEY=changeme`` (short and
low-entropy, which red-lines ``security.W009``) and the deploy check would fail
for the wrong reason.
"""

import os

# Set placeholders before importing production. production.py's _required_env()
# raises ImproperlyConfigured for any missing key, and base.py loads .env
# without overriding values that are already set.
#
# SECRET_KEY must be >=50 characters with >=5 unique characters or
# security.W009 turns the deploy gate red for a reason unrelated to this task.
os.environ.setdefault(
    "SECRET_KEY",
    "ci-only-insecure-key-0123456789-abcdefghij-ABCDEFGHIJ-xyz",
)
os.environ.setdefault("RDS_DB_NAME", "ci")
os.environ.setdefault("RDS_USERNAME", "ci")
os.environ.setdefault("RDS_PASSWORD", "ci")
os.environ.setdefault("RDS_HOSTNAME", "localhost")
os.environ.setdefault("RDS_PORT", "5432")
os.environ.setdefault("AWS_STORAGE_BUCKET_NAME", "ci-placeholder-bucket")

from config.settings.production import *  # noqa: E402,F401,F403

# No database is available in the CI test job (D006 leaves RDS absent in
# production too), so the dummy backend lets `check --deploy` and
# `makemigrations --check` run without a connection. The postgres driver is
# still installed via requirements/development.txt -- S05-T06's settings
# matrix boots config.settings.production in a subprocess, and importing the
# models under the postgresql ENGINE requires it.
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
