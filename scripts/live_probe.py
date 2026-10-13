#!/usr/bin/env python3
"""Live production probe for the M001/S02 endpoint repairs.

Stdlib-only smoke test against the deployed ``chemic-ally.xyz`` site. It
asserts:

* the apex serves HTTP 200;
* the trailing-slash ``/hplc/`` route serves HTTP 200, which proves the
  Function URL path reconciliation from S02/T02 is live (without it the
  route 301-loops);
* the DB-backed ``/hplc/api/levels/`` endpoint fails fast with an explicit,
  non-leaking HTTP 503 (D006: RDS-absent production). The request carries an
  ``Origin`` header so CloudFront forwards it the way a browser would
  (MEM009/L001); the body must be JSON with an ``error`` key and must not
  contain any internal exception detail;
* the calculator forms round-trip as real browser-shaped POSTs: the reaction
  balancer happy path (H2 + O2 -> H2O) returns HTTP 200 with the rendered
  result card, an impossible reaction still returns the explicit
  "Calculation Failed" card, and a valid equilibria system returns HTTP 200
  with a solved pH. The CSRF token is harvested from the form page's hidden
  input and the ``csrftoken`` cookie from that GET rides along in a shared
  cookie jar, with an ``Origin`` header because CloudFront does not forward
  ``Referer`` (MEM009/L001).

Set ``CHEMICALLY_PROBE_BASE_URL`` to point the probe at a local server; it
defaults to production. Each target prints one PASS/FAIL line; the process
exits non-zero if any check fails.
"""

import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get(
    "CHEMICALLY_PROBE_BASE_URL", "https://chemic-ally.xyz"
).rstrip("/")
ORIGIN = BASE_URL
TIMEOUT_SECONDS = 15
LEAK_MARKERS = ("Traceback", "OperationalError", "InterfaceError", "secret")
CSRF_TOKEN_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')

_COOKIES = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIES))

BALANCED_REACTION = {
    "reactant": "H2 O2",
    "product": "H2O",
    "reversible": "on",
}
IMPOSSIBLE_REACTION = {
    "reactant": "notAFormula",
    "product": "H2O",
    "reversible": "on",
}
VALID_EQUILIBRIUM = {
    "reactions": json.dumps(
        [
            {
                "reactants": "HCO3-",
                "products": "H+ + CO3-2",
                "k_mode": "pKa",
                "k_value": "10.3",
            },
            {
                "reactants": "H2CO3",
                "products": "H+ + HCO3-",
                "k_mode": "pKa",
                "k_value": "6.3",
            },
            {
                "reactants": "H2O",
                "products": "H+ + OH-",
                "k_mode": "pKa",
                "k_value": "14.0",
            },
        ]
    ),
    "concentrations": json.dumps({"HCO3-": {"value": 0.01, "unit": "mol/L"}}),
    "solvent": "H2O",
    "solvent_concentration": "55.4",
}


def _send(request):
    """Send a prepared request and return ``(status, body_text)``.

    HTTP error responses are returned rather than raised so their status and
    body can be asserted. Transport failures propagate to the caller.
    """
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _request(path, origin=None):
    """Perform a GET; the shared cookie jar keeps any CSRF cookie."""
    request = urllib.request.Request(BASE_URL + path)
    if origin is not None:
        request.add_header("Origin", origin)
    return _send(request)


def _post(path, data, origin=ORIGIN):
    """Submit a real urlencoded form POST through the CSRF flow.

    ChemicAlly's calculator views render the result directly on a valid POST
    (no redirect), so a successful submission is asserted as HTTP 200. The
    CSRF token is read from the form page's hidden input and the matching
    ``csrftoken`` cookie from that GET is replayed by the shared cookie jar.
    """
    page_status, page = _request(path, origin=origin)
    match = CSRF_TOKEN_RE.search(page)
    if match is None:
        raise RuntimeError(
            "no csrfmiddlewaretoken on {} (GET status={})".format(path, page_status)
        )
    fields = dict(data)
    fields["csrfmiddlewaretoken"] = match.group(1)
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(BASE_URL + path, data=body)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Origin", origin)
    return _send(request)


def _run(label, fn, validate):
    """Run one probe, print a PASS/FAIL line, and return its verdict."""
    try:
        status, body = fn()
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        print("FAIL: {} (transport error: {!r})".format(label, exc))
        return False
    ok, detail = validate(status, body)
    print("{}: {} ({})".format("PASS" if ok else "FAIL", label, detail))
    return ok


def _status_is(expected):
    def validate(status, body):
        return status == expected, "status={}".format(status)

    return validate


def _validate_db_api(status, body):
    problems = []
    if status != 503:
        problems.append("status={} (want 503)".format(status))
    payload = None
    try:
        payload = json.loads(body)
    except ValueError:
        problems.append("body is not JSON")
    if payload is not None and not (
        isinstance(payload, dict) and "error" in payload
    ):
        problems.append("JSON body has no 'error' key")
    leaked = [marker for marker in LEAK_MARKERS if marker in body]
    if leaked:
        problems.append("leaked marker(s): " + ", ".join(leaked))
    detail = "status={}".format(status)
    if problems:
        detail += "; " + "; ".join(problems)
    return not problems, detail


def _validate_balanced(status, body):
    problems = []
    if status != 200:
        problems.append("status={} (want 200)".format(status))
    if "ce{" not in body:
        problems.append("result card LaTeX ('ce{') missing")
    if "Calculation Failed" in body:
        problems.append("'Calculation Failed' shown for a valid reaction")
    return not problems, "; ".join(problems) or "status=200, result card present"


def _validate_calculation_failed(status, body):
    problems = []
    if status != 200:
        problems.append("status={} (want 200)".format(status))
    if "Calculation Failed" not in body:
        problems.append("'Calculation Failed' card missing")
    leaked = [marker for marker in LEAK_MARKERS if marker in body]
    if leaked:
        problems.append("leaked marker(s): " + ", ".join(leaked))
    return not problems, "; ".join(problems) or "status=200, error card present"


def _validate_equilibria(status, body):
    problems = []
    if status != 200:
        problems.append("status={} (want 200)".format(status))
    if "pH =" not in body:
        problems.append("solved pH result missing")
    if "Calculation Failed" in body:
        problems.append("'Calculation Failed' shown for a valid system")
    return not problems, "; ".join(problems) or "status=200, pH solved"


def main():
    checks = (
        (
            "GET / -> HTTP 200",
            lambda: _request("/"),
            _status_is(200),
        ),
        (
            "GET /hplc/ -> HTTP 200",
            lambda: _request("/hplc/"),
            _status_is(200),
        ),
        (
            "GET /hplc/api/levels/ (Origin) -> 503 non-leaking JSON",
            lambda: _request("/hplc/api/levels/", origin=ORIGIN),
            _validate_db_api,
        ),
        (
            "POST /calculate/reaction_balancer (H2 O2 -> H2O) -> 200 balanced",
            lambda: _post("/calculate/reaction_balancer", BALANCED_REACTION),
            _validate_balanced,
        ),
        (
            "POST /calculate/reaction_balancer (notAFormula) -> 200 error card",
            lambda: _post("/calculate/reaction_balancer", IMPOSSIBLE_REACTION),
            _validate_calculation_failed,
        ),
        (
            "POST /calculate/equilibria (valid system) -> 200 pH solved",
            lambda: _post("/calculate/equilibria", VALID_EQUILIBRIUM),
            _validate_equilibria,
        ),
    )

    all_ok = True
    for label, fn, validate in checks:
        all_ok = _run(label, fn, validate) and all_ok

    print("---")
    print("RESULT: " + ("ALL PASS" if all_ok else "FAILURES PRESENT"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
