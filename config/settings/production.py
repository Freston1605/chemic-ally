"""
Production settings for ChemicAlly.

Overrides and extends base settings for the production environment.
Expects all required secrets and credentials to be supplied via
environment variables (injected by Elastic Beanstalk or equivalent).
"""

import os

from django.core.exceptions import ImproperlyConfigured

from config.settings.base import LOGGING as BASE_LOGGING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _required_env(key: str) -> str:
    """Return the value of *key* or raise a descriptive error.

    Using this helper instead of bare ``os.environ[key]`` ensures the
    failing setting name is clearly visible in error reports and avoids
    confusion with other potential ``KeyError`` sources.
    """
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

ALLOWED_HOSTS = [
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

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True

CSRF_COOKIE_SECURE = True

SECURE_HSTS_SECONDS = 31536000  # 1 year — start with a smaller value when testing
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True

# X-XSS-Protection header is deliberately omitted — modern browsers have
# deprecated this feature and Django itself deprecated the related
# SECURE_BROWSER_XSS_FILTER setting in 5.0.

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
        # Persistent connections reduce per-request connection overhead at
        # the cost of holding a pool of connections open.  The value of 60
        # seconds matches common Elastic Beanstalk / proxy timeouts.
        "CONN_MAX_AGE": 60,
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

AWS_ACCESS_KEY_ID = _required_env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _required_env("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = _required_env("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "us-east-2")

AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN", "") or None

# Endpoint & protocol
AWS_S3_ENDPOINT_URL = f"https://s3.{AWS_S3_REGION_NAME}.amazonaws.com"
AWS_S3_USE_SSL = True
AWS_S3_VERIFY = True
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "virtual"

# Object-level cache and overwrite behaviour are configured per-backend
# inside config/storage_backends.py (StaticStorage, MediaStorage) so the
# module-level AWS_S3_OBJECT_PARAMETERS and AWS_S3_FILE_OVERWRITE are
# intentionally omitted here.


# ---------------------------------------------------------------------------
# Static files  (URL)
# ---------------------------------------------------------------------------

# When AWS_S3_CUSTOM_DOMAIN is set (CloudFront), use it; otherwise fall
# back to the direct S3 endpoint.
if AWS_S3_CUSTOM_DOMAIN:
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
else:
    STATIC_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/static/"


# ---------------------------------------------------------------------------
# Logging  (production-tuned)
# ---------------------------------------------------------------------------

LOGGING = {
    **BASE_LOGGING,
    "loggers": {
        # Suppress noisy middleware / health-check chatter at DEBUG/INFO
        # level.  WARNING and above are still forwarded to the root logger.
        "django.request": {
            "level": "WARNING",
        },
        "django.security": {
            "level": "WARNING",
        },
    },
}