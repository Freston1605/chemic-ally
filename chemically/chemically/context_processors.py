from typing import Dict, List
from django.http import HttpRequest


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
