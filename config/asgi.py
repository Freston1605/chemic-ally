"""
ASGI config for chemically project.

Exposes the ASGI callable as ``application`` and a Mangum-wrapped Lambda
handler as ``handler`` for deployment on AWS Lambda.
"""

import os

from django.core.asgi import get_asgi_application
from mangum import Mangum

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()

handler = Mangum(application, lifespan="off")
