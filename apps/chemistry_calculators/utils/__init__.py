"""Utility helpers for the chemistry_calculators package."""

import logging
from typing import Iterable

from django.db import InterfaceError, OperationalError
from django.http import HttpRequest

logger = logging.getLogger(__name__)

# Connection-level database failures only. Programming/integrity errors are
# deliberately not caught here so genuine bugs keep surfacing.
_SESSION_DB_ERRORS = (OperationalError, InterfaceError)


def add_previous_substances(request: HttpRequest, substances: Iterable[str]) -> None:
    """Persist a list of substance formulas to the session.

    Under D006 production runs without a database. A database-backed session
    store can raise a connection-level error when the session is read or
    written, and that write may happen outside the calling view's
    ``try``/``except`` (e.g. in ``SessionMiddleware.process_response``).
    Remembering history is a convenience, not part of the calculation, so a
    store that cannot be reached is logged and swallowed rather than allowed
    to fail the request.
    """
    try:
        prev = request.session.get("previous_substances", [])
        for substance in substances:
            if substance and substance not in prev:
                prev.append(substance)
        request.session["previous_substances"] = prev
    except _SESSION_DB_ERRORS:
        logger.exception(
            "Could not persist previous substances; session store unavailable."
        )
