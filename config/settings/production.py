from .base import *

DEBUG = False


ALLOWED_HOSTS += [
    "chemically-env.eba-pyxp2kzs.us-east-2.elasticbeanstalk.com",
    ".elasticbeanstalk.com",
    "chemic-ally.xyz",
]

SECRET_KEY = os.environ["SECRET_KEY"]

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["RDS_DB_NAME"],
        "USER": os.environ["RDS_USERNAME"],
        "PASSWORD": os.environ["RDS_PASSWORD"],
        "HOST": os.environ["RDS_HOSTNAME"],
        "PORT": os.environ["RDS_PORT"],
        # "CONN_MAX_AGE": 600,
    }
}

CSRF_TRUSTED_ORIGINS = [
    "https://chemically-env.eba-pyxp2kzs.us-east-2.elasticbeanstalk.com",
]


# ---------------------------------------------------------------------------
# AWS S3 – Static & Media Storage
# ---------------------------------------------------------------------------
# Modern configuration using the STORAGES dict (replaces the deprecated
# STATICFILES_STORAGE / DEFAULT_FILE_STORAGE pattern).
#
#  •  Static files  – publicly readable, served through optional CloudFront.
#  •  Media files   – private, served via temporary signed URLs (1 h TTL).
#  •  No AWS_DEFAULT_ACL  – S3 bucket policies handle access instead.
# ---------------------------------------------------------------------------

AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "us-east-2")

AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN", "") or None

# Endpoint & protocol
AWS_S3_ENDPOINT_URL = f"https://s3.{AWS_S3_REGION_NAME}.amazonaws.com"
AWS_S3_USE_SSL = True
AWS_S3_VERIFY = True
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "virtual"

# Object-level defaults
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "max-age=86400",
}

# Do not overwrite files with the same name (raise an error instead).
AWS_S3_FILE_OVERWRITE = False

# ---------------------------------------------------------------------------
# STORAGES  –  the modern Django 4.2+ way to declare backends
# ---------------------------------------------------------------------------
STORAGES = {
    "default": {
        "BACKEND": "config.storage_backends.MediaStorage",
    },
    "staticfiles": {
        "BACKEND": "config.storage_backends.StaticStorage",
    },
}

# Static files URL (used by the template engine for {% static %}).
# When AWS_S3_CUSTOM_DOMAIN is set (CloudFront), use it; otherwise fall
# back to the direct S3 endpoint.
if AWS_S3_CUSTOM_DOMAIN:
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
else:
    STATIC_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/static/"