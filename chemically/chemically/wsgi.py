"""
WSGI config for chemically project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/wsgi/
"""

import os
import sys
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler

path = os.path.expanduser("~/chemic-ally/chemically")
if path not in sys.path:
    path.insert(0, path)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chemically.settings')
application = get_wsgi_application()