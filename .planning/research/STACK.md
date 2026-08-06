# Stack Research — Security Hardening Milestone

**Domain:** Security hardening, API test coverage, and rate limiting for a Django 5.2 + Django REST Framework app deployed on AWS Lambda (Mangum, container image, PostgreSQL/RDS)
**Project:** ChemicAlly
**Researched:** 2026-08-05
**Confidence:** HIGH (versions verified against live PyPI data on 2026-08-05; library APIs verified via Context7/official source; AWS WAF behavior verified via AWS docs; chempy eval claim verified against chempy source)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Django (stay on 5.2 LTS) | **5.2.17** (currently pinned 5.2.3) | Web framework / security surface | 5.2 is the LTS line (supported to April 2028); the project is mid-hardening, so **do not jump to 6.x now**. Bump 5.2.3 → 5.2.17: 14 patch releases of bug/security fixes accumulated. Verified 5.2.17 is the current latest 5.2.x on PyPI. |
| Django REST Framework | **3.17.2** (currently pinned 3.15.2) | HPLC API + throttling | DRF 3.17.x is current (venv already has 3.17.1 — CONCERNS.md flags pin drift). 3.15→3.17 includes the built-in throttling behavior this milestone depends on. **Pin exactly** to stop the venv/CI drift already observed. |
| pytest-django | **4.13.0** (currently pinned 4.11.1) | Django test plugin | Current release; requires `pytest>=7.0`, Python ≥3.10 — compatible with pinned pytest 8.4.0 and Django 5.2. Powers the API test coverage (HARD-05/06) via `pytest.mark.django_db` and the `client` fixture. |
| bandit | **1.9.4** (new) | Python static security analysis | PyCQA's AST-based security linter — the de-facto standard for Python. Would have flagged the chempy `eval()` call site (B307). Zero-config baseline; config via `[tool.bandit]` in `pyproject.toml`; `-lll` severity filter for CI gating. |
| pip-audit | **2.10.1** (new; already referenced in CI) | Dependency vulnerability scanning | PyPA-maintained, free, no account, audits against PyPI/OSV advisory DB. Exit codes 0/1 and **cannot be suppressed internally** — directly fixes the CONCERNS.md `pip-audit ... || true` anti-pattern. Official GH Action `pypa/gh-action-pip-audit@v1.1.0`. |
| coverage.py + pytest-cov | **7.15.3** / **7.1.0** (new) | Test coverage measurement + gate | Standard Python coverage stack. Needed to make "API test coverage" (HARD-05) measurable and enforced in CI. `pytest --cov=apps --cov-fail-under=...`. |
| AWS WAF v2 (infrastructure, not a pip dep) | current (regional/global ACL) | Rate limiting / DoS boundary | CloudFront-integrated rate-based rules are the only *enforcement* boundary in this architecture (see Rate Limiting section). WAF rate-based rules: evaluation window 60–600s (default 300), min rate limit 10, IP aggregation. Verified via AWS docs. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| DRF built-in throttles (`ScopedRateThrottle`, `AnonRateThrottle`) | in DRF 3.17 | Per-endpoint request rate limits | Immediately (HARD-08). Configure `DEFAULT_THROTTLE_CLASSES` + `DEFAULT_THROTTLE_RATES` with per-view `throttle_scope`. No new dependency. |
| `django.core.signing` (`TimestampSigner`, `sign_object`) | stdlib (Django 5.2) | Server-side score anti-forgery (HARD-07) | Sign the `SimulateView` result payload; verify signature + `max_age` on `ScoreSubmissionView`. HMAC-sha256 bound to `SECRET_KEY`. No new dependency. |
| DRF `EXCEPTION_HANDLER` custom handler | in DRF 3.17 | Generic error responses, no exception-leak | Fix CONCERNS.md "SimulateView leaks internal exception details": wrap `rest_framework.views.exception_handler`, return generic body, log `str(e)` server-side. |
| chempy | **0.9.0** (keep pinned) | Chemistry engine (parsing is the RCE vector) | Keep for physics; **disable eval** via `rxn_parse_kwargs={"globals_": False}` on every string entry point (HARD-01). Do not bump to 0.10.x mid-hardening — version bump is its own risk. |
| pyparsing | **3.3.2** (currently pinned 3.2.3) | Formula grammar (chempy transitive) | Pure-grammar parsing — no eval. Bump optional; only if pip-audit flags 3.2.3. |
| mangum | ≥0.19 (keep; latest 0.21.0) | ASGI→Lambda adapter | No change needed for this milestone. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| flake8 7.0.0 | Lint (keep) | Do not migrate to ruff this milestone — Black-compatible config already wired into CI; ruff is a churn risk, not a security win. |
| bandit | Security lint in CI | Run `bandit -r apps config -lll -q`; block on HIGH/medium-confidence findings. |
| pip-audit | Dependency gate in CI | Replace `pip-audit ... || true` with a blocking step. |
| coverage/pytest-cov | Coverage gate | `--cov=apps/hplc_simulator --cov=apps/chemistry_calculators`; raise `--cov-fail-under` as coverage grows; start at a floor that doesn't block the milestone's own tests. |

## Mapping to CONCERNS.md Findings

| CONCERNS.md finding | Stack decision above | Status |
|---------------------|----------------------|--------|
| RCE via chempy `eval()` (critical) | `rxn_parse_kwargs={"globals_": False}` verified against chempy source; bandit B307 catches regressions | Recommended fix verified HIGH confidence |
| `LevelProgressSerializer` broken (500s) | pytest-django API tests for all 6 endpoints (HARD-05) | Test pattern verified |
| Client-supplied scores forgeable | `django.core.signing` TimestampSigner + server-side recompute | Verified API |
| No rate limiting | DRF throttles (app layer) + WAF rate rules (enforcement boundary) | Both verified |
| SimulateView leaks exception details | Custom `EXCEPTION_HANDLER` | Verified API |
| `check --deploy` runs with dev settings | Run with production settings (`--settings=config.settings.production` + env vars) | Verified |
| Non-blocking security scan (`|| true`) | pip-audit exit codes are 0/1 and non-suppressible — remove `\|\| true` | Verified |
| Unpinned ranges + observed drift | Pin DRF 3.17.2, bump Django 5.2.17; keep numpy/scipy ranges (engine constraints) | Versions verified live |
| Hardcoded fallback `SECRET_KEY` | `check --deploy` W009 + remove fallback in `base.py` | W009 verified in Django source |

## Static Analysis & Dependency Scanning

### bandit 1.9.4 — the security linter
- **What:** PyCQA AST-based static analysis; plugin checks per code construct (eval/subprocess/SQL/deserialization). Configuration lives in `pyproject.toml` under `[tool.bandit]` (`exclude_dirs`, `tests`, `skips`) — no new config file format.
- **Why:** The one tool that would have caught the chempy `eval()` call statically (bandit B307 "Use of possibly insecure function"). Runs in seconds on this codebase size. Official GitHub Action `PyCQA/bandit-action@v1` if you want SARIF uploads to the security tab.
- **CI gate:** `bandit -r apps config -lll -q` — `-lll` = report only HIGH severity (avoids noise blocking deploys); `-q` = quiet. Fail CI on any finding with a triage comment (existing false positives like `assert` usage get explicit skips in config, not blanket suppressions).

### pip-audit 2.10.1 — the dependency gate (replaces safety in practice)
- **What:** PyPA-maintained (Trail of Bits + Google backed); audits a requirements file or the installed environment against the PyPI/OSV advisory databases. Exit codes: `0` = no known vulns, `1` = vulns found. **The exit code cannot be suppressed by the tool itself** — `pip-audit ... || true` (current CI) must go.
- **CI gate:** `pip-audit -r requirements/requirements-lambda.txt` (or the aggregate pinned file once lockfile lands) with **no `|| true`**. For stricter integrity: `--require-hashes` forces hash-checking mode and fails on unpinned deps — pairs well with the CONCERNS.md "fully pin the transitive set" fix.
- **Why not safety:** **Safety 3.x is now a commercial product** (verified on its PyPI page, 2026-08-05): `safety scan` prompts for account creation/login, the free plan is "limited to a single user and is not recommended for commercial purposes," and paid plans start at $25/seat/month. The legacy `safety check` free tier is gone. Since pip-audit covers the same need with zero cost/account, **drop safety from CI entirely** — keeping two scanners where one is paywalled adds no security value.

### What the security-scan job should look like (target state)

```yaml
security-scan:
  steps:
    - uses: actions/checkout@v4
    - run: pip install bandit pip-audit
    - run: bandit -r apps config -lll -q          # fail on HIGH findings
    - run: pip-audit -r requirements/requirements-lambda.txt   # no || true
    - uses: pypa/gh-action-pip-audit@v1.1.0       # optional official action
```

### `manage.py check --deploy` — make it actually check production
- Django's `check --deploy` (verified in Django source) registers `deploy=True` checks: W009 (SECRET_KEY too short / `django-insecure-` prefix), W018 (DEBUG=True), W020 (empty ALLOWED_HOSTS), plus SecurityMiddleware (HSTS, SSL redirect, X-Frame-Options, nosniff), CSRF cookie security, session cookie security.
- CONCERNS.md is right that the current CI runs it against **development** settings (`DJANGO_SETTINGS_MODULE: config.settings.development` at deploy.yml:16) — with DEBUG=True every security check is skipped.
- **Fix:** run the check with production settings and the required env vars injected: `DJANGO_SETTINGS_MODULE=config.settings.production SECRET_KEY=<ci-secret> RDS_DB_NAME=... python manage.py check --deploy`. Use a throwaway CI secret (not the prod one) — the check only inspects settings values, it doesn't connect to anything.

## API Test Stack (HARD-05, HARD-06)

### Pattern: pytest-django 4.13.0 + DRF `APIClient`
- **Keep pytest 8.4.0** (pytest 9.1.1 is current but 8.4 is stable and pytest-django 4.13 requires only `pytest>=7`). A pytest major-bump is unrelated churn for a hardening milestone.
- Use **DRF's `APITestCase` / `APIClient`** (not plain Django `Client`) for the six `/hplc/api/*` endpoints. `APIClient` extends Django's `Client` and adds `format='json'` serialization — the project already tests at the engine/model layer, so the missing piece is exactly the view layer.
- **Session-keyed endpoints** (`api/scores/`, `api/progress/`): the Django test client maintains the session cookie across requests — `client.post(...)` then `client.get(...)` on the same client exercises the real session-key → `LevelProgressView` path (the current 500). No `force_authenticate` needed (anonymous session identity); `force_login`/`force_authenticate` are there if account auth ever lands.
- **Isolation:** `@pytest.mark.django_db` wraps each test in a transaction rolled back after — no cross-test pollution of `UserScore`/`LevelProgress`. Use `@pytest.mark.django_db(transaction=True)` only for tests that genuinely need commits (e.g., simulating the score-submission-then-progress flow inside one test is fine without it).
- **Settings overrides:** the `settings` fixture (`def test_x(settings): settings.THROTTLE_RATES[...] = ...`) is the sanctioned way to disable throttling in tests (set `DEFAULT_THROTTLE_RATES` to `'10000/min'` or override `DEFAULT_THROTTLE_CLASSES` to `[]`) — otherwise your own rate limit will 429 your test suite.

### Required API test matrix (from CONCERNS.md "API layer has zero test coverage")

| Endpoint | What the test must assert |
|----------|---------------------------|
| `GET /hplc/api/levels/` | 200, pagination shape (`PageNumberPagination`), seeded Analyte/Level data present |
| `GET /hplc/api/levels/<slug>/` | 200 for valid slug; 404 for unknown slug |
| `POST /hplc/api/simulate/` | 200 + chromatogram shape for valid params; 400 for invalid params; **generic error body, no `str(e)` leak** (assert `'detail'` does not contain file paths / library internals) |
| `POST /hplc/api/scores/` | 201/200 on valid submission; **rejects forged/tampered score** once HARD-07 lands; invalid fields → 400 with field errors |
| `GET /hplc/api/scores/history/` | 200, per-session scoping (session A cannot see session B's scores) |
| `GET /hplc/api/progress/` | **200 with a session** (regression for the LevelProgressSerializer bug), 200 empty list without a session |

### Adversarial input regression tests (HARD-06) — the RCE guard
- `POST /equilibria` (server-rendered form) with hidden `reactions` JSON containing `k_value` payloads: `"; __import__('os').system('id')"`, `"__import__('os')"`, `"10**-1; print(1)"`, oversized strings beyond the field `max_length`.
- Assert the request completes without code execution: no new response side effects, no exception leaking the payload, clean 200/400. These tests are the permanent tripwire for the HARD-01 fix.

### Coverage gate
- `pytest --cov=apps --cov-report=term-missing --cov-fail-under=<floor>` via `pytest-cov`; start the floor where the milestone's own new tests land (API views + equilibria form), then ratchet per phase. Coverage is a tripwire, not a goal — the RCE regression tests are the goal.

## Rate Limiting on Lambda (HARD-08) — Layered, Serverless-Aware

There is **no single mechanism** that both enforces and is cheap on this stack (no Redis by project constraint). The correct design is two layers:

### Layer 1 (enforcement boundary): CloudFront + AWS WAF v2 rate-based rules
- WAF rate-based rules (verified via AWS docs): evaluation window 60/120/**300**/600 seconds, rate limit per aggregation instance (**minimum 10**), aggregation by IP (`RateKey: IP`). CloudFront distributions accept an associated Web ACL in the SAM/CloudFormation template.
- **This is the only true DoS defense** — it blocks at the edge, before Lambda is invoked, so the CPU-bound `SimulateView` (5000-point numpy/scipy chromatogram) cannot be hammered into Lambda cost abuse.
- Suggested starting limits: `2000/300s` on `/hplc/api/simulate/` (CPU-heavy), `5000/300s` globally. Tune from CloudWatch; WAF applies limits "near but not exactly" the configured value — that's acceptable for abuse prevention.
- **Critical caveat the audit missed:** the Lambda Function URL is public (`AuthType: NONE`, `template.yaml`). CloudFront is in front, but an attacker can **call the Function URL directly**, bypassing CloudFront/WAF entirely. WAF only protects traffic that goes through CloudFront. Options, in order of preference:
  1. Restrict the Function URL to CloudFront-originated traffic (e.g., require a custom header only CloudFront sets, reject otherwise) — cheap, closes the bypass.
  2. Move origin from Function URL to API Gateway HTTP API (WAF-associable, `AuthType` none/`AWS_IAM`), with CloudFront in front.
  3. Accept direct-Function-URL exposure as documented risk (not recommended — the app layer is the only defense then).
  For this milestone, option 1 (custom header check in a tiny middleware/ASGI wrapper) is the pragmatic fix; flag option 2 for the infra follow-up.

### Layer 2 (app layer): DRF throttling — defense-in-depth + per-endpoint policy
- **Use `ScopedRateThrottle`** as the global default with per-view `throttle_scope`, or `AnonRateThrottle` (the API is 100% anonymous). Example:

```python
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.ScopedRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {
        'simulate': '30/min',     # CPU-bound
        'scores':   '20/min',     # write path / RDS bloat
        'read':     '120/min',    # levels, history, progress
    },
}
```

- `throttle_scope = 'simulate'` on `SimulateView`, `'scores'` on `ScoreSubmissionView`, `'read'` on the read endpoints. This directly answers CONCERNS.md's "no rate limiting" finding and API4:2023 (Unrestricted Resource Consumption).
- **Serverless caveats (verified from DRF docs/source):**
  - **Client identity:** DRF uses `X-Forwarded-For` if present, else `REMOTE_ADDR`; `NUM_PROXIES` (default `None`) controls how strictly. Behind CloudFront the viewer IP arrives in `X-Forwarded-For`, so per-client throttling works **for traffic through CloudFront**.
  - **Cache backend:** throttles store history in Django's cache. Default `LocMemCache` is **per-process** — on Lambda each container keeps its own counter, so limits are *approximate* (a client can get ~N × concurrency requests) and reset on cold start. That's why WAF (Layer 1) is the enforcement boundary and DRF throttling is policy/defense-in-depth.
  - **XFF spoofing:** DRF trusts `X-Forwarded-For`. Anyone hitting the Function URL directly can spoof it. Layer 1's header check closes this.
  - **No Redis:** adding ElastiCache/Redis just for throttling is explicitly out of scope (project constraint: no Redis). If precise global throttling ever becomes a requirement, that is the trigger to revisit — not now.

### What about WAF vs. django-ratelimit vs. django-axes?
- **django-ratelimit** (4.1.0 exists) — view-decorator rate limiting, fine for server-rendered views, but DRF has first-class throttling already; don't add a second in-process limiter.
- **django-axes** (8.3.1 exists) — login brute-force protection. The app has **no login** (session identity only). Skip.
- Both would sit in the same per-process cache trap on Lambda — same approximation problem, no edge blocking. Not worth it.

## Secure-by-Default String Parsing (HARD-01) — chempy/pyparsing

### The verified fact
Verified against chempy source (`chempy/chemistry.py`, `Reaction.from_string`):

```
globals_ : dict (optional)
    Dictionary for eval for (default: None -> {'chempy': chempy})
    If ``False``: no eval will be called (useful for web-apps).
...
Notes
-----
:func:`chempy.util.parsing.to_reaction` is used which in turn calls
:func:`eval` which is a severe security concern for untrusted input.
```

The CONCERNS.md claim is accurate. `EqSystem.from_string` (equilibria) and `ReactionSystem.from_string` both pass `rxn_parse_kwargs` through to `Reaction.from_string`, so the fix is:

```python
EqSystem.from_string(
    reaction_string,
    rxn_parse_kwargs={"globals_": False},   # disables eval entirely
)
```

### The stack rule: every string path into chempy must pass `globals_=False`
- `apps/chemistry_calculators/calculations/equilibria.py:102` (`EqSystem.from_string`) — the live RCE.
- Add a module-level guard/helper (single choke point) rather than sprinkling kwargs — the CONCERNS.md "Fragile Area" note says keep reconstruction in one place; same logic applies to parsing.
- **Verify at implementation time** that chempy **0.9.0**'s `EqSystem.from_string` signature accepts `rxn_parse_kwargs` (verified on master; 0.9.0 is the pinned version — a quick REPL check settles it). If 0.9.0 differs, options: (a) keep 0.9.0 and wrap parsing yourself, (b) bump to 0.10.1 with a full test pass. Do not assume.
- **Defense in depth (not instead of `globals_=False`):**
  - Validate `k_value` as a float server-side (`float(x)` rejecting everything else) before string interpolation into `10**-{k_value}` — CONCERNS.md's own recommendation.
  - Add `max_length` to the hidden `reactions`/`concentrations` CharFields (the form currently validates only non-emptiness).
  - Keep bandit B307 in CI as a static tripwire for any future `eval`-adjacent code.
- **pyparsing** (3.3.2 latest; pinned 3.2.3): the formula grammar is a real parser (no eval) — the RCE is solely the chempy `to_reaction` path. No action needed beyond keeping the pin honest.

### Score anti-forgery (HARD-07) — `django.core.signing`, no new dependency
- Current flaw: `ScoreSubmissionView` persists client-supplied `score`/`min_resolution`/`total_run_time`/`max_pressure_bar`/`overpressure` (CONCERNS.md). 
- Recommended stack: **recompute the score server-side** from the simulation parameters using the engine's `calculate_score` (deterministic once the RNG is seeded — pair with the CONCERNS.md "unseeded RNG" fix), **and** sign the `SimulateView` result with `TimestampSigner.sign_object(...)` (HMAC-sha256, bound to `SECRET_KEY`); `ScoreSubmissionView` verifies via `unsign_object(token, max_age=...)` (raises `SignatureExpired`/`BadSignature`). Verified API from `django/topics/signing.txt`.
- This is OWASP API3:2023 (Broken Object Property Level Authorization) — the client must not be authoritative for server-computed properties.

## Serverless-Aware Security Practices (beyond rate limiting)

| Practice | Stack choice | Why |
|----------|-------------|-----|
| Error responses don't leak internals | Custom DRF `EXCEPTION_HANDLER` wrapping `rest_framework.views.exception_handler`; return generic body; `logger.exception()` server-side | CONCERNS.md: `{'error': ..., 'detail': str(e)}` leaks paths/library internals to anonymous callers |
| No secrets in settings defaults | Remove fallback `SECRET_KEY` in `base.py` (mirror `production.py`'s `ImproperlyConfigured`); W009 via `check --deploy` | CONCERNS.md: committed-looking fallback key |
| Migrate secrets to SSM | Move `RDSPassword`/`DjangoSecretKey` from `--parameter-overrides` plaintext to SSM Parameter Store secure strings (same pattern as existing `RDSDBName` etc.) | CONCERNS.md: plaintext SAM overrides in deploy.yml |
| `check --deploy` against production settings | Set `DJANGO_SETTINGS_MODULE=config.settings.production` + env vars in the test job | CONCERNS.md: current run is a no-op |
| Migrations actually run | Add a `manage.py migrate` step in deploy (production settings, RDS env vars from secrets) | CONCERNS.md: "missing critical feature" — not a library, but the milestone's schema changes (`UserScore` anti-forgery columns if any) need it |
| Least-privilege SG | Restrict Lambda SG ingress to RDS SG only; tighten egress | CONCERNS.md: `172.31.0.0/16` all-ports ingress |
| Error visibility | Sentry (`sentry-sdk` 2.66.1) or CloudWatch alarms on 5XX — deferred; not required for this milestone | CONCERNS.md: no error-reporting; keep out of scope to bound the milestone |

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| pip-audit (free, OSV/PyPI) | safety 3.x | Only if you need Safety DB's commercial feed/malicious-package coverage AND are willing to pay ($25/seat/mo) + manage API keys in CI. Not worth it here. |
| flake8 (keep) | ruff | Later, as a full lint+format migration — not during a hardening milestone; ruff 0.16.1 is current and could replace flake8+black eventually. |
| DRF built-in throttles | django-ratelimit | If the server-rendered calculator forms (non-DRF) need limits — but they're cheap GETs/POSTs behind CloudFront; the expensive surface is the DRF `simulate` endpoint. Revisit only if form abuse shows up in metrics. |
| Django 5.2 LTS | Django 6.1 | Next LTS cycle (6.x LTS arrives ~April 2027). Revisit after the hardening milestone + a deprecation audit. |
| WAF + DRF throttles (2-layer) | Single layer (app-only throttling) | App-only is insufficient on Lambda: per-container LocMemCache makes limits approximate and XFF is spoofable via direct Function URL access. |
| `django.core.signing` tokens | Full auth/user accounts | Out of scope by project decision (PROJECT.md); signing is the minimal anti-forgery fix. Accounts are a separate milestone. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **safety 3.x** | Verified 2026-08-05: requires account/login (`safety scan` prompts), free plan is single-user / "not recommended for commercial purposes", paid from $25/seat/mo. The old free `safety check` CI model is dead. | pip-audit 2.10.1 (free, PyPA, exit codes 0/1) |
| **`pip-audit ... || true` / `safety check ... || true`** | The exact CONCERNS.md anti-pattern — swallows every finding; pip-audit's non-zero exit is the feature | `pip-audit -r requirements/requirements-lambda.txt` bare, fail the job |
| **chempy string parsing without `globals_=False`** | Live unauthenticated RCE (eval in `to_reaction`) | `rxn_parse_kwargs={"globals_": False}` at every entry point + float-validated `k_value` + length caps |
| **`{{ result|safe }}` on user-derived LaTeX** | User input + `|safe` is one parser-loosening away from XSS (CONCERNS.md) | Escape or build LaTeX from structured tokens; keep `|safe` only for engine-generated, non-user-derived strings |
| **Redis/ElastiCache just for throttling** | New infra + ops burden; project constraint says no Redis; per-process LocMemCache approximation is acceptable because WAF is the boundary | WAF rate rules + DRF throttles |
| **django-axes** | Login brute-force tool for an app with no login | Nothing — not applicable |
| **Django 6.x mid-milestone** | 5.2 LTS is supported to 2028; a major upgrade during an RCE-hardening milestone risks the fix itself | Stay on 5.2.17 |
| **gunicorn in requirements** | EB-era leftover; Lambda runs Mangum (CONCERNS.md) | Delete gunicorn from `requirements/production.txt`; simplify the requirements graph to one file per environment |

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| Django 5.2.17 | DRF 3.17.2, pytest-django 4.13.0, mangum ≥0.19, chempy 0.9.0 | 5.2.x is the current LTS line; DRF 3.17 supports Django 4.2/5.x. |
| pytest-django 4.13.0 | pytest ≥7.0 (keep 8.4.0), Python ≥3.10 | Verified from PyPI metadata; Django 5.2 supported. |
| pip-audit 2.10.1 | Python ≥3.10 (Lambda runtime is 3.14) | CI-only dependency; keep out of the Lambda image to slim it. |
| bandit 1.9.4 | Python ≥3.9 | CI-only. |
| numpy ≥2.1,<3 / scipy ≥1.14,<2 | engine uses `np.trapezoid` (needs ≥2.0) | Keep ranges honest (CONCERNS.md); venv has numpy 2.4.3/scipy 1.17.1 — in range. Do not narrow without a full engine test pass. |
| chempy 0.9.0 | pyparsing 3.2.3/3.3.2 | **Verify 0.9.0's `EqSystem.from_string` accepts `rxn_parse_kwargs` at implementation** (verified on master; pin is 0.9.0). |
| DRF throttles | Django cache framework | Uses `default` cache (LocMemCache per container on Lambda) — approximation is expected and acceptable behind WAF. |
| pytest-cov 7.1.0 / coverage 7.15.3 | pytest 8.4.0 | Standard pairing; `--cov-fail-under` gate. |

## Installation

```bash
# Dev/test additions (requirements/development.txt)
pip install -r requirements/development.txt
pip install pytest-cov==7.1.0 coverage==7.15.3 bandit==1.9.4 pip-audit==2.10.1

# Pin updates (requirements/base.txt)
#   Django==5.2.17        (from 5.2.3)
#   djangorestframework==3.17.2   (from 3.15.2 — aligns with venv 3.17.1)
#   pyparsing==3.3.2      (optional bump from 3.2.3)

# CI security scan (no || true)
bandit -r apps config -lll -q
pip-audit -r requirements/requirements-lambda.txt
DJANGO_SETTINGS_MODULE=config.settings.production SECRET_KEY=<ci-secret> \
  RDS_DB_NAME=x RDS_USERNAME=x RDS_PASSWORD=x RDS_HOSTNAME=x RDS_PORT=5432 \
  python manage.py check --deploy
```

## Sources

- PyPI JSON API (pypi.org/pypi/<pkg>/json) — **verified live versions 2026-08-05**: Django 5.2.17 / 6.1, DRF 3.17.2, pytest-django 4.13.0, bandit 1.9.4, pip-audit 2.10.1, safety 3.8.1, coverage 7.15.3, pytest-cov 7.1.0, chempy 0.10.1 (project pins 0.9.0), pyparsing 3.3.2, mangum 0.21.0, ruff 0.16.1, django-ratelimit 4.1.0, django-axes 8.3.1, sentry-sdk 2.66.1 — HIGH confidence
- Context7 `/encode/django-rest-framework` (docs/api-guide/throttling.md, testing.md, exceptions.md, settings.md) — throttle classes, XFF/NUM_PROXIES, cache backend, EXCEPTION_HANDLER, APIClient patterns — MEDIUM (verified against official docs)
- Context7 `/django/django` (core/checks/security/base.py, topics/signing.txt, docs/ref/django-admin.md) — W009/W018/W020 deploy checks, Signer/TimestampSigner/sign_object API — MEDIUM
- chempy source `chempy/chemistry.py` (github.com/bjodah/chempy, master) — `Reaction.from_string` `globals_=False` + eval warning, verbatim — **HIGH** (primary source)
- pip-audit PyPI project page — exit codes, GH Action, `--require-hashes`, security model — MEDIUM
- safety PyPI project page — commercial licensing/account model verified — **HIGH** (primary source)
- AWS docs (docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-high-level-settings.html) — rate-based rule windows/limits/aggregation — MEDIUM
- OWASP API Security Top 10 2023 (owasp.org/API-Security/editions/2023/en/) — risk taxonomy (API3/API4/API8/API10) — MEDIUM
- Project files: `.planning/PROJECT.md`, `.planning/codebase/STACK.md`, `.planning/codebase/CONCERNS.md` — grounding for all mappings

---
*Stack research for: security hardening milestone (HARD-01 through HARD-08)*
*Researched: 2026-08-05*





