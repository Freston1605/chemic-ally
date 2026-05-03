from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Use SQLite for local development to avoid PostgreSQL latency and simplify setup.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

# Set to True for local development to avoid slow static file lookups
WHITENOISE_AUTOREFRESH = True
