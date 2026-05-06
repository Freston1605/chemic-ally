from .base import *

DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "chemically-env.eba-pyxp2kzs.us-east-2.elasticbeanstalk.com",
).split()
SECRET_KEY = os.environ["SECRET_KEY"]

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


# Static files (CSS, JavaScript, Images)
# AWS S3 setting
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME')
AWS_DEFAULT_ACL = 'public-read'
AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}

# s3 static settings
AWS_LOCATION = 'static'
STATIC_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/{AWS_LOCATION}/'
STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'