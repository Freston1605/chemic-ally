"""DRF exception handling for the HPLC simulator API.

Under D006 the production deployment intentionally runs without a
database (no RDS instance is provisioned). Any DB-backed endpoint then
raises a connection-level ``django.db.OperationalError`` (or
``InterfaceError``) instead of returning a response. Without this
handler DRF would surface an unhandled 500, and the raw driver text
would leak internal connection detail to the client.

The handler maps only connection-level failures to an explicit,
non-leaking 503. Programming/Integrity errors are *not* caught here so
genuine bugs keep surfacing as 500s.
"""

import logging

from django.db import InterfaceError, OperationalError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

DB_UNAVAILABLE_ERROR = (
    "The simulator database is unavailable. Please try again later."
)

# Connection-level failures that mean "the database cannot be reached",
# as opposed to a bad query or a constraint violation.
_CONNECTION_ERRORS = (OperationalError, InterfaceError)


def api_exception_handler(exc, context):
    """Return a non-leaking 503 for unreachable-database errors.

    Any other exception is delegated to DRF's default handler so
    validation errors, permission denials, and genuine bugs keep their
    usual semantics.
    """
    if isinstance(exc, _CONNECTION_ERRORS):
        logger.exception("Simulator database unavailable")
        return Response({'error': DB_UNAVAILABLE_ERROR}, status=503)
    return exception_handler(exc, context)
