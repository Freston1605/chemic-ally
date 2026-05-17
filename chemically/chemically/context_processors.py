from typing import Dict, List
from django.http import HttpRequest

__all__ = ["previous_substances", "current_url"]


def previous_substances(request: HttpRequest) -> Dict[str, List[str]]:
    """Return previously viewed substances stored in the session.

    Parameters
    ----------
    request : HttpRequest
        The current HTTP request object.

    Returns
    -------
    dict
        Dictionary with key ``'previous_substances'`` mapping to a list of
        substance strings stored in the session (defaults to an empty list).
    """
    return {
        "previous_substances": request.session.get("previous_substances", [])
    }


def current_url(request: HttpRequest) -> Dict[str, str]:
    """Return the current URL name for active nav highlighting.

    Parameters
    ----------
    request : HttpRequest
        The current HTTP request object.

    Returns
    -------
    dict
        Dictionary with key ``'current_url'`` mapping to the resolved
        URL name (empty string if not resolved).
    """
    url_name = ""
    if hasattr(request, "resolver_match") and request.resolver_match:
        url_name = request.resolver_match.url_name or ""
    return {"current_url": url_name}
