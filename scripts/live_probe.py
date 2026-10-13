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
  contain any internal exception detail.

Each target prints one PASS/FAIL line; the process exits non-zero if any
check fails.
"""

import json
import sys
import urllib.error
import urllib.request

BASE_URL = "https://chemic-ally.xyz"
ORIGIN = BASE_URL
TIMEOUT_SECONDS = 15
LEAK_MARKERS = ("Traceback", "OperationalError", "InterfaceError", "secret")


def _request(path, origin=None):
    """Perform a GET and return ``(status, body_text)``.

    HTTP error responses are returned rather than raised so their status and
    body can be asserted. Transport failures propagate to the caller.
    """
    request = urllib.request.Request(BASE_URL + path)
    if origin is not None:
        request.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


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
    )

    all_ok = True
    for label, fn, validate in checks:
        all_ok = _run(label, fn, validate) and all_ok

    print("---")
    print("RESULT: " + ("ALL PASS" if all_ok else "FAILURES PRESENT"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
