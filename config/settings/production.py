from .base import *

DEBUG = False
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split()
SECRET_KEY = os.environ.get("SECRET_KEY")
