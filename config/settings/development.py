from .base import *  # noqa: F403

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
]


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases
# Use SQLite for local development to avoid PostgreSQL latency and simplify setup.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"

# Set to True for local development to avoid slow static file lookups
WHITENOISE_AUTOREFRESH = True


# Media files

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'


# STORAGES
# https://docs.djangoproject.com/en/5.0/ref/settings/#storages

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
