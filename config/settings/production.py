from .base import *

DEBUG = False
ALLOWED_HOSTS = [
    "http://chemically-env.eba-pyxp2kzs.us-east-2.elasticbeanstalk.com/",
]
SECRET_KEY = os.environ.get("SECRET_KEY")

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