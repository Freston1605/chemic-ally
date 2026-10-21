"""
Production settings for ChemicAlly.

Overrides and extends base settings for the production environment.
Expects all required secrets and credentials to be supplied via
environment variables (injected by the Lambda runtime or environment).
"""

import os
import re

from django.core.exceptions import ImproperlyConfigured
from config.settings.base import *  # noqa: F403
from config.settings.base import LOGGING as BASE_LOGGING
from config.settings.base import REST_FRAMEWORK as BASE_REST_FRAMEWORK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _required_env(key: str) -> str:
    """Return the value of *key* or raise a descriptive error."""
    try:
        return os.environ[key]
    except KeyError:
        raise ImproperlyConfigured(
            f"Production setting requires environment variable '{key}' to be set."
        ) from None


# ---------------------------------------------------------------------------
# Core Django settings
# ---------------------------------------------------------------------------

DEBUG = False

SECRET_KEY = _required_env("SECRET_KEY")

_allowed = os.environ.get("ALLOWED_HOSTS", ".chemic-ally.xyz")
ALLOWED_HOSTS += [h.strip() for h in _allowed.split() if h.strip()]  # noqa: F405

CSRF_TRUSTED_ORIGINS = [
    "https://chemic-ally.xyz",
    "https://www.chemic-ally.xyz",
]


# ---------------------------------------------------------------------------
# HTTPS / SSL Security
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True

# CloudFront / Function URL passes X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# D006: the calculators are intentionally DB-less in production (no RDS
# instance is provisioned). The default database-backed session engine would
# make every calculator POST fail with an OperationalError inside
# SessionMiddleware.process_response, outside the view's try/except, surfacing
# as an HTTP 500. Sign the session data into the cookie instead so no
# request ever needs a database. CSRF continues to use its own cookie (the
# default); CSRF_USE_SESSIONS is deliberately left off.
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'

SECURE_HSTS_SECONDS = 31536000  # 1 year — start with a smaller value when testing
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"


# ---------------------------------------------------------------------------
# Database  (PostgreSQL via AWS RDS)
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _required_env("RDS_DB_NAME"),
        "USER": _required_env("RDS_USERNAME"),
        "PASSWORD": _required_env("RDS_PASSWORD"),
        "HOST": _required_env("RDS_HOSTNAME"),
        "PORT": _required_env("RDS_PORT"),
        "CONN_MAX_AGE": 0,
    }
}


# ---------------------------------------------------------------------------
# Cache  (M006/S02 -- DRF scoped throttle backstop)
# ---------------------------------------------------------------------------
# The throttle state must be shared by every Lambda container, so production
# keeps Django's DatabaseCache on the RDS-backed default database inherited
# from base.py. Django's default LocMemCache is per-process: with N warm
# containers the effective limit would be N times the configured rate and a
# burst would never be throttled.
#
# The cache table is created by the deploy migrator (config/migrate.py runs
# ``createcachetable`` right after ``migrate``), and
# scripts/run_migrator.py fails the deploy when the migrator does not report
# that bootstrap -- a fresh environment must never serve traffic with the
# throttle's table missing.


# ---------------------------------------------------------------------------
# Throttle rates  (M006/S02 -- R027/R028)
# ---------------------------------------------------------------------------
# The rates are the operational knob of the in-app backstop, so the deployed
# environment can tune them without a code change while base.py keeps the
# reviewable defaults. A malformed value fails the boot here instead of
# reaching DRF, where an unparsable rate raises at request time.
_RATE_PATTERN = re.compile(r"[0-9]+/[0-9]*[smhd][a-z]*")


def _throttle_rate(env_var, default):
    """Return the configured rate for *env_var*, or *default* when unset."""
    value = os.environ.get(env_var, default).strip()
    if not _RATE_PATTERN.fullmatch(value):
        raise ImproperlyConfigured(
            f"Production setting {env_var} must look like '<requests>/<period>' "
            f"(for example '30/min'); got {value!r}."
        )
    return value


_BASE_RATES = BASE_REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']

REST_FRAMEWORK = {
    **BASE_REST_FRAMEWORK,
    'DEFAULT_THROTTLE_RATES': {
        **_BASE_RATES,
        'simulate': _throttle_rate(
            'THROTTLE_RATE_SIMULATE', _BASE_RATES['simulate']
        ),
        'scores': _throttle_rate(
            'THROTTLE_RATE_SCORES', _BASE_RATES['scores']
        ),
    },
}


# ---------------------------------------------------------------------------
# STORAGES  (S3 via django-storages)
# ---------------------------------------------------------------------------

STORAGES = {
    "default": {
        "BACKEND": "config.storage_backends.MediaStorage",
    },
    "staticfiles": {
        "BACKEND": "config.storage_backends.StaticStorage",
    },
}


# ---------------------------------------------------------------------------
# AWS S3 configuration
# ---------------------------------------------------------------------------

# Use .get() for keys. The Lambda execution role handles S3 access
# automatically — hardcoded keys should be left unset in production.
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")

AWS_STORAGE_BUCKET_NAME = _required_env("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "us-east-2")
AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN", "") or None

AWS_S3_ENDPOINT_URL = f"https://s3.{AWS_S3_REGION_NAME}.amazonaws.com"
AWS_S3_USE_SSL = True
AWS_S3_VERIFY = True
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "virtual"


# ---------------------------------------------------------------------------
# Logging  (production-tuned)
# ---------------------------------------------------------------------------

LOGGING = {
    **BASE_LOGGING,
    "loggers": {
        "django.request": {
            "level": "WARNING",
        },
        "django.security": {
            "level": "WARNING",
        },
    },
}
