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

_mangum = Mangum(application, lifespan="off")


def handler(event, context):
    """Lambda entry point.

    AWS Lambda Function URLs (payload v2) normalize
    ``requestContext.http.path`` by stripping the trailing slash, while
    ``rawPath`` preserves it. Mangum reads ``requestContext.http.path`` for
    API Gateway v2 events, so Django sees the slash-stripped path and
    ``CommonMiddleware`` (``APPEND_SLASH``) issues a 301 back to the
    trailing-slash URL. The Function URL then normalizes that request again,
    producing an infinite 301 self-redirect on every trailing-slash route
    (``/hplc/``, ``/hplc/api/*``, ``/admin/``).

    Restore the original path from ``rawPath`` for Function URL v2 events
    before delegating to Mangum. Every other event shape (ALB, API Gateway
    v1, local tests) passes through unchanged.
    """
    if (
        event.get("version") == "2.0"
        and event.get("rawPath")
        and event.get("requestContext", {}).get("http", {}).get("path")
    ):
        event["requestContext"]["http"]["path"] = event["rawPath"]
    return _mangum(event, context)
