"""Utility helpers for the webapp package."""

from typing import Iterable
from django.http import HttpRequest


def add_previous_substances(request: HttpRequest, substances: Iterable[str]) -> None:
    """Persist a list of substance formulas to the session."""
    prev = request.session.get("previous_substances", [])
    for substance in substances:
        if substance and substance not in prev:
            prev.append(substance)
    request.session["previous_substances"] = prev
