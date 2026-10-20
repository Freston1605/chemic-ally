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

The live score-token tamper check (R013/R014) mints a real token from
``POST /hplc/api/simulate/`` using a level id harvested from the DB-backed
``GET /hplc/api/levels/``, submits it to ``POST /hplc/api/scores/`` (status
recorded verbatim -- 201, or the D006 503), then submits a
single-character-tampered copy that must be refused with the uniform
non-disclosing 400. When the levels endpoint 503s under D006 no level id
exists, so no token can be minted: the check then prints exactly one SKIP
line -- never a pass, never a failure.
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


# ---------------------------------------------------------------------------
# Live score-token tamper check (R013/R014, S05-T06)
# ---------------------------------------------------------------------------

# The browser client's simulate shape (mirrors the ``valid_simulate_payload``
# fixture in tests/hplc_simulator/conftest.py): level_id plus mobile_phase,
# column and operation configs.
SIMULATE_MOBILE_PHASE = {
    "start_b": 5,
    "end_b": 95,
    "ramp_time": 20,
    "ph": 3.0,
    "buffer_concentration_mm": 25,
    "is_gradient": True,
}
SIMULATE_COLUMN = {
    "chemistry": "C18",
    "length_mm": 150,
    "id_mm": 4.6,
    "particle_size_um": 5.0,
}
SIMULATE_OPERATION = {
    "flow_rate_ml_min": 1.0,
    "temperature_c": 30,
    "injection_volume_ul": 10,
    "dwell_time_min": 1.0,
}

# The single uniform, non-disclosing rejection body
# (hplc_simulator.views.SCORE_REJECTION_ERROR).
UNIFORM_TOKEN_REJECTION = "Invalid or expired result token"

# Printed verbatim (exactly one line) when the DB-backed levels endpoint is
# the documented D006 503, so no signed token can be obtained. A SKIP is
# neither a pass nor a failure: the tamper/replay/persistence claims stay
# unit-proven until an environment with a database exercises them.
SKIP_TAMPER_LINE = (
    "SKIP: live score-token tamper check "
    "(db-backed /hplc/api/levels/ returns 503 under D006; "
    "no level id available) — replay/persistence claims remain unit-proven"
)


def _csrf_token():
    """Return the csrftoken cookie value from the shared jar, if any.

    This is exactly the harvest ``_post`` performs for the calculator form
    pages, applied to the DRF surface: whatever the server put in the shared
    cookie jar is what gets injected. For /hplc/api/ nothing is ever set
    (DRF APIViews are csrf_exempt), and the probe says so instead of
    assuming the CSRF contract.
    """
    for cookie in _COOKIES:
        if cookie.name == "csrftoken":
            return cookie.value
    return None


def _post_json(path, payload, csrf_token=None):
    """POST a JSON body with an Origin header (MEM009/L001).

    DRF APIViews are csrf_exempt, so no CSRF header is required; the token is
    sent only when the server actually set the cookie, and the observation
    is recorded in the check's output line.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(BASE_URL + path, data=data)
    request.add_header("Content-Type", "application/json")
    request.add_header("Origin", ORIGIN)
    if csrf_token:
        request.add_header("X-CSRFToken", csrf_token)
    return _send(request)


def _extract_level_id(body):
    """First active level id from the levels payload, or None."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if isinstance(payload, list):
        levels = payload
    elif isinstance(payload, dict):
        levels = payload.get("results") or []
    else:
        return None
    for level in levels:
        if isinstance(level, dict) and level.get("id") is not None:
            return level["id"]
    return None


def _tamper_token(token):
    """Flip one character inside the token (index computed, not pasted)."""
    index = len(token) // 2
    replacement = "A" if token[index] != "A" else "B"
    return token[:index] + replacement + token[index + 1:]


def _validate_token_rejection(status, body, token):
    """Assert the tampered POST got the uniform non-disclosing 400."""
    problems = []
    if status != 400:
        problems.append("status={} (want 400, not 503/500)".format(status))
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
        problems.append("body is not JSON")
    if payload is not None:
        if set(payload) != {"error"}:
            problems.append(
                "body keys={} (want exactly ['error'])".format(sorted(payload))
            )
        elif payload["error"] != UNIFORM_TOKEN_REJECTION:
            problems.append(
                "body error={!r} (want {!r})".format(
                    payload["error"], UNIFORM_TOKEN_REJECTION
                )
            )
    if token and token in body:
        problems.append("rejection body echoes the submitted token")
    leaked = [marker for marker in LEAK_MARKERS if marker in body]
    if leaked:
        problems.append("leaked marker(s): " + ", ".join(leaked))
    return not problems, "; ".join(problems) or "uniform non-disclosing 400"


def _live_score_token_tamper_check():
    """Live proof that the deployed site refuses a forged result_token.

    Returns True (PASS), False (FAIL), or None (SKIP: the DB-backed
    ``/hplc/api/levels/`` endpoint returns the D006 503, so no level id --
    and therefore no server-signed token -- can be obtained).
    """
    try:
        # 1. A level id from the real, DB-backed levels endpoint. Under D006
        #    this endpoint answers the non-leaking 503 and the check cannot
        #    run: SKIP with exactly one line. Never invent a level id, and
        #    never treat the 503 as a pass of the tamper contract.
        levels_status, levels_body = _request(
            "/hplc/api/levels/", origin=ORIGIN
        )
        if levels_status == 503:
            print(SKIP_TAMPER_LINE)
            return None
        level_id = _extract_level_id(levels_body) if levels_status == 200 else None
        if level_id is None:
            print(
                "FAIL: live score-token tamper check (levels endpoint "
                "returned status={} with no usable level id)".format(
                    levels_status
                )
            )
            return False

        # 2. Mint a real token from the live simulate endpoint, using the
        #    browser client's payload shape and the shared-jar CSRF harvest.
        simulate_payload = {
            "level_id": level_id,
            "mobile_phase": SIMULATE_MOBILE_PHASE,
            "column": SIMULATE_COLUMN,
            "operation": SIMULATE_OPERATION,
        }
        _request("/hplc/api/simulate/", origin=ORIGIN)
        csrf = _csrf_token()
        sim_status, sim_body = _post_json(
            "/hplc/api/simulate/", simulate_payload, csrf_token=csrf
        )
        try:
            sim = json.loads(sim_body)
        except ValueError:
            sim = {}
        token = sim.get("result_token") if isinstance(sim, dict) else None
        if sim_status != 200 or not isinstance(token, str) or not token:
            print(
                "FAIL: live score-token tamper check (simulate endpoint "
                "returned status={} body={!r})".format(
                    sim_status, sim_body[:200]
                )
            )
            return False

        # 3. The legitimate submission: 201 is the goal; the D006 503 is
        #    tolerated and recorded verbatim, because token verification runs
        #    before any DB access and the 400 below is the actual contract.
        metrics = sim.get("metrics") or {}
        submission = {
            "result_token": token,
            "level_id": level_id,
            "total_run_time": metrics.get("total_run_time"),
            "min_resolution": metrics.get("min_resolution"),
            "max_pressure_bar": metrics.get("max_pressure_bar"),
            "mobile_phase": SIMULATE_MOBILE_PHASE,
            "column_config": SIMULATE_COLUMN,
            "operation_config": SIMULATE_OPERATION,
        }
        _request("/hplc/api/scores/", origin=ORIGIN)
        csrf = _csrf_token()
        valid_status, valid_body = _post_json(
            "/hplc/api/scores/", submission, csrf_token=csrf
        )
        if valid_status not in (201, 503):
            print(
                "FAIL: live score-token tamper check (valid token POST "
                "returned status={} body={!r})".format(
                    valid_status, valid_body[:200]
                )
            )
            return False

        # 4. The tamper: one flipped character must be refused with the
        #    uniform non-disclosing 400 -- before any DB access, so this
        #    assertion holds even in the D006-degraded production.
        tampered = _tamper_token(token)
        submission["result_token"] = tampered
        tampered_status, tampered_body = _post_json(
            "/hplc/api/scores/", submission, csrf_token=csrf
        )
        ok, rejection_detail = _validate_token_rejection(
            tampered_status, tampered_body, tampered
        )
        if csrf:
            csrf_note = (
                "csrftoken cookie set by /hplc/api/; sent as X-CSRFToken"
            )
        else:
            csrf_note = (
                "no csrftoken cookie set by /hplc/api/; no CSRF header sent "
                "-- CSRF not required, observed empirically"
            )
        if valid_status == 201:
            valid_note = "accepted (201)"
        else:
            valid_note = "D006 503 recorded verbatim"
        detail = (
            "valid token POST -> {} ({}); tampered token POST -> {} ({}); "
            "[{}]".format(
                valid_status, valid_note, tampered_status, rejection_detail,
                csrf_note,
            )
        )
        print(
            "{}: live score-token tamper check ({})".format(
                "PASS" if ok else "FAIL", detail
            )
        )
        return ok
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        print(
            "FAIL: live score-token tamper check (transport error: {!r})".format(
                exc
            )
        )
        return False


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

    # The tamper check runs outside ``_run``: it owns its one-line SKIP
    # output, and a SKIP is neither a pass nor a failure.
    skipped = False
    tamper_outcome = _live_score_token_tamper_check()
    if tamper_outcome is False:
        all_ok = False
    elif tamper_outcome is None:
        skipped = True

    print("---")
    if all_ok:
        if skipped:
            print(
                "RESULT: ALL PASS "
                "(1 check skipped: live score-token tamper check)"
            )
        else:
            print("RESULT: ALL PASS")
    else:
        print("RESULT: FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
