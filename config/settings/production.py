"""
Production settings for ChemicAlly.

Overrides and extends base settings for the production environment.
Expects all required secrets and credentials to be supplied via
environment variables (injected by Elastic Beanstalk or equivalent).
"""

import os
from django.core.exceptions import ImproperlyConfigured
from config.settings.base import *  # noqa: F403
from config.settings.base import LOGGING as BASE_LOGGING


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

ALLOWED_HOSTS += [  # noqa: F405
    ".elasticbeanstalk.com",
    ".chemic-ally.xyz",
]

CSRF_TRUSTED_ORIGINS = [
    "https://*.elasticbeanstalk.com",
    "https://chemic-ally.xyz",
    "https://www.chemic-ally.xyz",
]


# ---------------------------------------------------------------------------
# HTTPS / SSL Security
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True

# Perfectly configured for the Nginx proxy passing X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

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
        "CONN_MAX_AGE": 60,
        # Ensures stale connections don't crash requests (Requires Django 4.1+)
        "CONN_HEALTH_CHECKS": True,
    }
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

# Use .get() for keys. The EC2 instance should use an IAM Instance Profile
# to access S3 automatically, rather than hardcoding access keys in env vars.
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
