from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

SECRET_KEY = os.environ["SECRET_KEY"]

ALLOWED_HOSTS += [
    ".elasticbeanstalk.com",
    ".chemic-ally.xyz",
]

CSRF_TRUSTED_ORIGINS = [
    "https://*.us-east-2.elasticbeanstalk.com",
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

SECURE_HSTS_SECONDS = 31536000  # 1 year — but start with a smaller value if testing
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

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


# STORAGES
# https://docs.djangoproject.com/en/5.0/ref/settings/#storages
# Modern configuration using the STORAGES dict (replaces the deprecated
# STATICFILES_STORAGE / DEFAULT_FILE_STORAGE pattern).
#
#  •  Static files  – publicly readable, served through optional CloudFront.
#  •  Media files   – private, served via temporary signed URLs (1 h TTL).
#  •  No AWS_DEFAULT_ACL  – S3 bucket policies handle access instead.

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


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/
# When AWS_S3_CUSTOM_DOMAIN is set (CloudFront), use it; otherwise fall
# back to the direct S3 endpoint.

if AWS_S3_CUSTOM_DOMAIN:
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
else:
    STATIC_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/static/"
