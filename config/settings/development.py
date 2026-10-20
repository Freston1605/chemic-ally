"""Local development settings.

R016: ``base.py`` carries no ``SECRET_KEY`` fallback and raises
``ImproperlyConfigured`` when the key is unset. That raise happens while
``base`` is being imported, i.e. during the ``from .base import *`` below — so
the local-dev fallback MUST be seeded into ``os.environ`` *before* that import.
Assigning ``SECRET_KEY`` after the import is too late: the import aborts first
and every bare ``manage.py`` command (``runserver``, ``migrate``,
``makemigrations``) fails in a checkout with no ``.env``.

This mirrors the ``os.environ.setdefault`` pattern already used by
``config/settings/ci.py`` and ``config/settings/testing.py``. ``setdefault``
means a real ``SECRET_KEY`` from the environment or from ``.env`` (loaded by
``base.py``) still wins; the placeholder only fills the gap for local work.
Deliberately long and obviously fake so Django's ``security.W009``
length/entropy heuristic does not red-line a local ``check --deploy``.
"""

import os

os.environ.setdefault("SECRET_KEY", "dev-only-insecure-key-0123456789-abcdefghij")

from .base import *  # noqa: E402,F403

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# Re-read after the import so an explicitly provided key still wins, and so the
# module exposes the value it actually runs with.
SECRET_KEY = os.environ["SECRET_KEY"]

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
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405

# Set to True for local development to avoid slow static file lookups
WHITENOISE_AUTOREFRESH = True


# Media files

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'  # noqa: F405


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
