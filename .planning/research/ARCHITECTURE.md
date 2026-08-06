# Architecture Research

**Domain:** Security hardening of an existing Django 5.2 + DRF chemistry web app on AWS Lambda (Mangum, RDS, no Redis/Celery)
**Researched:** 2026-08-05
**Confidence:** MEDIUM-HIGH — every library-mechanics claim below was verified directly against primary sources (chempy/DRF/Django source, pip-audit README, AWS WAF docs); design synthesis (component boundaries, build order) is the researcher's recommendation. Digests cached in the research store with per-claim confidence.

**Scope:** Hardening milestone only. Not a rewrite. All changes must respect: Lambda 1024 MB / 30s timeout, `CONN_MAX_AGE=0`, session-based identity (no auth model), public Function URL (`AuthType: NONE`), no Redis/Celery.

---

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EDGE LAYER (CloudFront + WAF)                        │
│   CloudFront distribution ── WAF WebACL ── rate-based rule (scoped to        │
│   /hplc/api/* + /equilibria) — blocks abuse BEFORE Lambda compute cost      │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ (viewer IP appended to X-Forwarded-For)
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                      HTTP BOUNDARY (Lambda + Mangum)                         │
│   config/asgi.py handler → config/urls.py ('' → chemistry, '/hplc/' → api)  │
├─────────────────────────────────────────────────────────────────────────────┤
│   INPUT SANITIZATION GATE (webapp)         INPUT SANITIZATION GATE (API)     │
│   Django Form.clean():                      DRF Serializer.validate():        │
│   • structural JSON checks                  • field types/ranges/units        │
│   • charset regex (formula)                 • cross-field rules               │
│   • float() + isfinite (K values)           • length caps                     │
│   • length caps (max_length)                                                  │
├───────────────────────────────────────┬─────────────────────────────────────┤
│   ENGINE BOUNDARY (defense-in-depth)   │  SCORE INTEGRITY LAYER              │
│   EquilibriaCalculator._is_safe_equation│  SimulateView: run engine → sign    │
│   → EqSystem.from_string(              │    result → return token            │
│     rxn_parse_kwargs={"globals_":      │  ScoreSubmissionView: unsign →      │
│       {"__builtins__": {}}})           │    verify → recompute → persist     │
│   (single scoring module — no drift)   │                                     │
├───────────────────────────────────────┴─────────────────────────────────────┤
│   THROTTLE LAYER (DRF)                                                       │
│   ScopedRateThrottle + custom session/IP throttles → cache.get/set           │
├─────────────────────────────────────────────────────────────────────────────┤
│   PERSISTENCE (PostgreSQL/RDS, CONN_MAX_AGE=0)                               │
│   App tables (Analyte, Level, UserScore, LevelProgress) +                    │
│   django_cache table (DatabaseCache — shared throttle state, no Redis)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Implementation |
|-----------|----------------|----------------|
| CloudFront WAF (edge) | Coarse per-IP rate limiting; stops CPU-abuse before Lambda; no compute cost | Rate-based rule scoped to `/hplc/api/*` + `/equilibria`; aggregate on source IP; 5-min window; `template.yaml` |
| Input sanitization gate (forms) | Reject malicious/pathological input at the HTTP boundary; build chempy strings in ONE place | `EquilibriumSystemForm.clean` — `float()`+`isfinite()` on K, charset regex on formulas, `max_length` on hidden JSON fields (already in working tree) |
| Input sanitization gate (serializers) | Same role for the JSON API | DRF `validate_*` methods; add length caps to simulate payload |
| Engine boundary | **Last line of defense before chempy.** Re-validates strings even when called without a form (unit tests, future API) | `_is_safe_equation()` regex gate + `EqSystem.from_string(rxn_parse_kwargs={"globals_": {"__builtins__": {}}})` (already in working tree) |
| Scoring module | Single source of truth for score math | Consolidate `engine.calculate_score()` + `scoring.evaluate_run()` into one module; engine returns raw metrics only |
| Result signer | Issues unforgeable simulation results | `signing.dumps({level_slug, metrics, score, issued_at}, salt='hplc-result')` in `SimulateView` |
| Result verifier | Accepts only server-issued results; recomputes score; persists | `ScoreSubmissionView` — `unsign(max_age=...)`, level check, recompute from signed metrics, anti-replay unique token hash |
| Throttle layer | Per-view request-rate limits keyed by session/IP | `ScopedRateThrottle` + custom `SessionRateThrottle`; backed by DatabaseCache |
| Cache store | Shared, durable throttle state (no Redis) | `django.core.cache.backends.db.DatabaseCache` on existing RDS; `createcachetable` |
| CI/CD gates | Block deploys that are un-migrated, vulnerable, or security-flagged | `makemigrations --check`, `migrate --check`, blocking `pip-audit`, `check --deploy` against production settings, `migrate` before `sam deploy` |

---

## Recommended Project Structure

The hardening work adds files but keeps the existing two-app layout. Proposed additions:

```
apps/
├── chemistry_calculators/
│   ├── calculations/
│   │   ├── equilibria.py          # (modified) engine-boundary validator + globals_ gate
│   │   └── security.py            # NEW: shared charset regexes (imported by forms + engine)
│   └── forms.py                   # (modified) strict K-value validation, length caps
├── hplc_simulator/
│   ├── simulation/
│   │   ├── engine.py              # (modified) return raw metrics; call scoring module
│   │   └── scoring.py             # (modified) becomes THE scoring module (delete dup in engine)
│   ├── security/
│   │   ├── results.py             # NEW: sign_result() / verify_result() (signing.dumps wrapper)
│   │   └── throttles.py           # NEW: SessionRateThrottle, SimulateRateThrottle, XFF-aware IP throttle
│   ├── models.py                  # (modified) UserScore.result_token_hash (unique, anti-replay)
│   └── views.py                   # (modified) SimulateView signs; ScoreSubmissionView verifies
├── config/
│   ├── settings/
│   │   ├── base.py                # (modified) CACHES DatabaseCache + REST_FRAMEWORK throttles
│   │   └── ci.py                  # NEW: imports production, supplies dummy env for check --deploy
│   └── ...
tests/
├── chemistry_calculators/
│   └── test_webapp.py             # (modified) adversarial/regression RCE tests (in working tree)
└── hplc_simulator/
    ├── api/
    │   ├── test_levels.py         # NEW per-endpoint suites
    │   ├── test_simulate.py
    │   ├── test_scores.py         # incl. forged-token rejection
    │   ├── test_progress.py       # incl. the 500 regression (HARD-02)
    │   ├── test_history.py
    │   └── test_throttling.py     # N+1 calls → 429
    └── security/
        └── test_result_integrity.py  # NEW: tamper, replay, overpressure bypass
```

### Structure Rationale

- **`calculations/security.py` shared regexes:** the form and the engine currently duplicate the same charset regexes (working tree). Extract once so the two gates cannot drift — if the form is loosened, the engine gate still holds.
- **`hplc_simulator/security/` package:** keeps signing and throttling out of views.py; views stay thin HTTP adapters. Matches the existing "pure logic decoupled from HTTP" convention.
- **`tests/hplc_simulator/api/` split by endpoint:** one file per resource mirrors the URLconf and makes a failing endpoint's test obvious; the current single test dir is why the broken `LevelProgressSerializer` shipped unnoticed.
- **`config/settings/ci.py`:** the production settings module hard-fails on missing env vars, so `check --deploy` cannot run in CI against it as-is. A thin `ci.py` that imports production and injects dummy `SECRET_KEY`/RDS values unblocks the real security checks without weakening production code.

---

## Architectural Patterns

### Pattern 1: Defense-in-depth input gate before an eval-based parser

**What:** Two independent validation layers between user input and `EqSystem.from_string`: (1) form/serializer-level semantic validation, (2) engine-level syntactic validation with eval stripped to no builtins. The engine re-validates even when the form was bypassed.

**When to use:** Any time untrusted strings reach a library that internally calls `eval` (chempy `to_reaction` is the documented case).

**Trade-offs:** Slightly duplicated validation logic (mitigated by sharing the regex module). Payoff: the engine stays safe for all callers (unit tests, future API endpoints), not just the HTTP path.

**Critical implementation detail (verified in chempy source):** `to_reaction` does `param = None if globals_ is False else eval(param, globals_)`. So passing `globals_=False` (as originally recommended in CONCERNS.md) **silently sets the equilibrium constant to `None`** and breaks the calculation. The correct fix — already in the working tree — is `rxn_parse_kwargs={"globals_": {"__builtins__": {}}}`: eval still runs on the *validated numeric* K expression (arithmetic on literals needs no builtins) but every name lookup/call (`__import__`, `os`, …) raises `NameError`.

**Second critical detail (verified in chempy source):** `globals_=False` does NOT neutralize a third `;`-separated segment — `to_reaction` still calls `eval("dict(" + ";".join(parts[2:]) + ")", globals_ or {})`. With the stripped-builtins dict, that eval also fails (no `dict` name), which is why **the dict approach is strictly stronger than `False`** for the extra-segment path too.

```python
# apps/chemistry_calculators/calculations/equilibria.py (working-tree pattern)
for equation in equations:
    if not _is_safe_equation(equation):            # regex: one '=', no extra ';', K charset
        raise ValueError("Unsafe or malformed reaction string")
eqsys = EqSystem.from_string(
    "\n".join(equations),
    rxn_parse_kwargs={"globals_": {"__builtins__": {}}},
)
```

### Pattern 2: Server-issued signed result token (anti-forgery without Redis)

**What:** `SimulateView` computes the simulation, derives the score, and returns a cryptographically signed token (Django `signing.dumps`, HMAC-SHA256 over `SECRET_KEY`) containing the level slug + metrics + score + issued timestamp. `ScoreSubmissionView` accepts **only** the token, verifies signature and age (`unsign(max_age=...)`), checks the level, recomputes the score from the signed metrics, and persists. Client-submitted `score`/`min_resolution`/`is_successful`/`overpressure` fields are ignored entirely.

**When to use:** Any anonymous/trustless game-score API. Pure "recompute from client metrics" is insufficient — a user can lie about `min_resolution` to inflate the recomputed score. Signing proves the metrics came from our server.

**Trade-offs:** One extra signing step (~µs) and a `max_age` replay window. Replay without Redis is bounded by `max_age` (e.g. 10–30 min) or eliminated with a unique `result_token_hash` column on `UserScore` (DB unique constraint = one-time-use token). Recommended: keep the unique column — it is one migration and kills replay outright.

**Anti-replay note:** the current `overpressure` flag is the only consistency check in `UserScore.clean` and is client-supplied — signed results remove it from the trust boundary.

```python
# hplc_simulator/security/results.py
from django.core import signing

SALT = "hplc-result"
MAX_AGE = 30 * 60

def sign_result(level_slug: str, metrics: dict, score: dict) -> str:
    return signing.dumps(
        {"level": level_slug, "metrics": metrics, "score": score}, salt=SALT
    )

def verify_result(token: str) -> dict:
    return signing.loads(token, salt=SALT, max_age=MAX_AGE)  # BadSignature / SignatureExpired
```

### Pattern 3: Shared-cache throttling on serverless with no Redis

**What:** DRF's `SimpleRateThrottle` is a cache-based implementation — `allow_request` does `cache.get(key, [])` + `cache.set(key, history, duration)` on the Django cache. On Lambda with concurrent warm containers, the default `LocMemCache` is **per-instance and ineffective** (each container has its own count). The no-new-infra fix is to point the default cache at the existing RDS via `DatabaseCache`, which is shared and durable.

**When to use:** Multi-instance serverless Django/DRF with an RDS already present and no appetite for Redis.

**Trade-offs:** Each throttle check = 1 DB read + 1 DB write per request. Acceptable here: `CONN_MAX_AGE=0` already opens a fresh connection per request, and this app is low-traffic. `DatabaseCache` culls expired rows on every `add/set/touch` (Django 5.x: `MAX_ENTRIES` default 300, `CULL_PERCENTAGE` default 3), so the table stays bounded under throttle traffic. Requires `createcachetable` (idempotent) in the deploy pipeline.

**IP identity behind CloudFront (verified in DRF source):** `get_ident()` reads `X-Forwarded-For` and, with `NUM_PROXIES=1`, returns the **last** XFF entry — which CloudFront appends as the real viewer IP. Set `NUM_PROXIES = 1` in settings, or anonymous throttles key on CloudFront edge IPs (all users share one bucket). **Spoofing note:** CloudFront overwrites XFF for the viewer hop, so the last entry is not client-controlled.

```python
# config/settings/base.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "chemically_throttle_cache",
    }
}
REST_FRAMEWORK = {
    "NUM_PROXIES": 1,
    "DEFAULT_THROTTLE_CLASSES": [
        "hplc_simulator.security.throttles.SessionRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "simulate": "20/min",     # CPU-bound endpoint
        "scores": "10/min",       # RDS-write endpoint
        "anon": "120/min",        # coarse fallback
    },
}
```

**Edge vs app split:** CloudFront WAF rate-based rules (aggregate on source IP, 5-min window, Block action, up to 10,000 managed IPs, scopeable to a path pattern) stop abuse **before** Lambda — no compute, no DB writes. App-level DRF throttles provide session-scoped semantics WAF cannot. Both layers are cheap; the WAF rule is the primary defense against anonymous hammering, the DRF throttle the secondary semantic layer.

### Pattern 4: Endpoint-mirrored API test coverage

**What:** One `APITestCase` class per endpoint, organized by URL, plus a dedicated security suite. Standard contract tests (happy path, validation 400, engine failure 500) plus regression tests pinned to known bugs.

**When to use:** Every DRF API. The project shipped a 500-ing `/hplc/api/progress/` and a forgeable score endpoint with zero API tests — this pattern would have caught both at CI time.

**Trade-offs:** More files; mitigated by mirroring the URLconf so the mapping is mechanical. **Throttling in tests:** wrap the suite in `override_settings(REST_FRAMEWORK={...})` with `DEFAULT_THROTTLE_RATES` disabled (or very high) so contract tests are fast; write exactly one throttle test that hits an endpoint `N+1` times and asserts 429. Force session identity via the test client's cookie handling (Django test client persists the session cookie across requests in a `TestCase`).

### Pattern 5: Migration + security CI/CD gates

**What:** Four gates added to the deploy pipeline, all verified against Django/pip-audit docs:

1. `python manage.py makemigrations --check` — exits 1 if model changes lack a migration (`--check` implies `--dry-run`; verified in Django source).
2. `python manage.py check --deploy` — runs W009/W018/W020 security checks **only with `deploy=True`**, and only meaningfully against the production settings module (the workflow currently runs it against `development` settings, which skips the checks entirely).
3. `pip-audit` **without** `|| true` — exits 0 clean / 1 on known vulnerabilities (verified exit-code contract); `--ignore-vuln ID` allowlists accepted advisories. The current workflow swallows failures.
4. `python manage.py migrate` (+ `createcachetable` once the DB cache lands) **before** `sam deploy` — applied against production settings with RDS env vars from secrets, so new code never runs against the old schema. `migrate --check` fails fast when unapplied migrations exist.

**Trade-offs:** Blocking `pip-audit` can stall deploys on advisories with no fix — hence the explicit allowlist workflow (review + add `--ignore-vuln`). Migrate-before-deploy is the standard single-function serverless ordering (no blue/green to coordinate).

---

## Data Flow

### Request Flow — equilibria calculator (RCE-safe path)

```
Browser POST /equilibria (hidden JSON reactions/concentrations)
    ↓
EquilibriumSystemForm.clean()
    • JSON parse + structural checks (list of dicts, expected keys)
    • per-reaction: float(k_value) + isfinite(k_value)      ← load-bearing fix
    • per-reaction: charset regex on reactants/products      ← blocks extra ';' segments
    • max_length on reactions/concentrations fields          ← caps payload size
    ↓ (reaction strings built HERE, in the form, never in the view)
EquilibriaCalculator.calculate(equations, concentrations)
    ↓
_is_safe_equation(equation) — re-validates at engine boundary
    ↓
EqSystem.from_string(reaction_string,
    rxn_parse_kwargs={"globals_": {"__builtins__": {}}})     ← eval locked to literals
    ↓
root() → equilibrium concentrations → LaTeX result
```

**Security invariant:** a payload like `{"reactants":"H2O","products":"H+ + OH-","k_value":"__import__('os').system(...)"}` dies at the form (`float()` fails) *and* would die at the engine (`_is_safe_equation` K-charset fails) *and* could not execute even if both were bypassed (no builtins in eval globals). Three independent stops.

### Data Flow — score submission (anti-forgery)

```
POST /hplc/api/simulate/  (level slug + simulation params)
    ↓
SimulateView: serialize → engine.generate_chromatogram() → raw metrics
    ↓
scoring.calculate_score(metrics)          ← single scoring module
    ↓
sign_result(level_slug, metrics, score)   ← signing.dumps, salt='hplc-result'
    ↓
200 {token, chromatogram, score, ...}     ← token held by client
    ...
POST /hplc/api/scores/  {token, level_slug}
    ↓
ScoreSubmissionView:
    verify_result(token)                   ← unsign(max_age=30min); BadSignature/SignatureExpired → 400
    level_slug in token == request's level → 400 on mismatch
    recompute score from SIGNED metrics    ← client metrics fields ignored
    UserScore(result_token_hash=sha256(token)) — IntegrityError → 400 (replay rejected)
    ↓
LevelProgress upsert (best score/resolution/time, attempts, completed)
```

### Key Data Flows

1. **Validation → chempy:** form builds strings → engine re-validates → chempy parses with no builtins. Direction is strictly one-way; the view never constructs reaction strings.
2. **Simulate → persist:** engine → score → sign → client → verify → recompute → persist. The client is a stateless token courier; it never provides numeric claims.
3. **Throttle:** request → WAF (edge) → DRF throttle (RDS cache) → view. Two independent counters at different cost tiers.

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Current (0–1k users/day) | DatabaseCache throttling + WAF rule is correct and free. No pooler needed. |
| 1k–100k users/day | RDS Proxy in front of RDS (`CONN_MAX_AGE=0` × Lambda concurrency will exhaust `max_connections` first); consider moving throttle cache to ElastiCache Redis only if DB write amplification from throttles becomes measurable; cap `UserScore` history per session. |
| 100k+ users/day | Edge WAF rate rules stay; app throttles become Redis-backed; move scoring/session identity to real accounts; split the API app out. |

### Scaling Priorities

1. **First bottleneck:** RDS connection exhaustion (Lambda concurrency × connection-per-request) — add RDS Proxy before anything else.
2. **Second bottleneck:** unbounded `UserScore` rows and session payloads — cap history per session/level and run `clearsessions` (add a scheduled job or management command).

---

## Anti-Patterns

### Anti-Pattern 1: `globals_=False` as the chempy RCE fix

**What people do:** Read chempy's docstring ("If `False`: no eval will be called — useful for web-apps") and pass `globals_=False`.
**Why it's wrong (verified in source):** `to_reaction` sets `param = None if globals_ is False else eval(...)` — the K value is silently discarded and every equilibria calculation produces `param=None` (breaks `float(elem)` in `root()`, or silently wrong results). Additionally, a third `;` segment is still `eval`'d with `globals_ or {}` — an empty globals dict still exposes builtins, so `dict(...)`-style injection survives.
**Do this instead:** `rxn_parse_kwargs={"globals_": {"__builtins__": {}}}` — eval stays available for validated numeric literals but every name/call fails. This is what the working tree does; keep it.

### Anti-Pattern 2: Trusting client-submitted score metrics

**What people do:** Accept `score`, `min_resolution`, `is_successful`, `overpressure` from the request body (current `ScoreSubmissionView`).
**Why it's wrong:** Fully forgeable — `score: 999999999` tops every level; the `overpressure` flag bypasses the only cross-field check. With a public Function URL, this is trivially exploitable by script.
**Do this instead:** Signed result token (Pattern 2). Recompute from server-signed metrics; ignore client numerics.

### Anti-Pattern 3: LocMemCache-backed DRF throttles on Lambda

**What people do:** Follow DRF's docs ("LocMemCache should be okay for simple setups") and ship `AnonRateThrottle` with the default cache.
**Why it's wrong:** On Lambda each warm container has its own LocMemCache; throttling becomes per-instance, so an attacker spreads requests across containers and never gets limited. Also the default `REMOTE_ADDR` identity is the CloudFront edge IP, not the viewer.
**Do this instead:** `DatabaseCache` on RDS + `NUM_PROXIES=1` (Pattern 3) + a WAF rate rule at the edge.

### Anti-Pattern 4: Duplicated scoring implementations

**What people do:** Keep `calculate_score()` in `engine.py` and `evaluate_run()` in `scoring.py` (current state — `scoring.py` is unused).
**Why it's wrong:** The two copies will drift; when score recomputation becomes security-critical (anti-forgery), a drift between the signing path and the verification path means the server rejects its own results or accepts forged arithmetic.
**Do this instead:** One scoring module. `engine.py` returns raw metrics; `scoring.py` owns score/success math; both `SimulateView` and `ScoreSubmissionView` import it.

### Anti-Pattern 5: Security scans with `|| true` and `check --deploy` on dev settings

**What people do:** `pip-audit ... || true` (current workflow) and `DJANGO_SETTINGS_MODULE=development` for the deploy check.
**Why it's wrong:** Known-vulnerability findings never gate deploys; `check --deploy` security checks (W009/W018/W020) are `deploy=True`-only and silently skip under dev settings — the check is theater.
**Do this instead:** Drop `|| true`, use `--ignore-vuln` for accepted advisories, run `check --deploy` against `config.settings.ci` (production import + dummy env).

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| RDS PostgreSQL | Django ORM + `DatabaseCache` (same DB) | `createcachetable` before first throttle use; `MAX_ENTRIES`/`CULL_PERCENTAGE` defaults fine for throttle load |
| CloudFront + WAF | `template.yaml` WebACL + rate-based rule | Scope rule to `/hplc/api/*` + `/equilibria`; 5-min window; block action; 10,000-IP cap per rule |
| Lambda Function URL | Mangum ASGI | `AuthType: NONE` stays — throttling + signing, not auth, is the hardening lever |
| PyPI advisory DB | `pip-audit` in CI | Exit 1 blocks deploy; `--ignore-vuln` allowlist reviewed per advisory |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Form ↔ `calculations/security.py` | direct import (shared regexes) | Keeps the two gates from drifting |
| View ↔ Engine | `.calculate()` / `generate_chromatogram()` call | Engine stays Django-free; returns raw metrics only |
| SimulateView ↔ `security/results.py` | `sign_result()` | Signing is a pure function of computed metrics + SECRET_KEY |
| ScoreSubmissionView ↔ `security/results.py` + `scoring.py` | `verify_result()` + `calculate_score()` | Verify-then-recompute-then-persist; never trust request body numerics |
| Views ↔ Throttles | `throttle_classes`/`throttle_scope` on APIView | `simulate` scope on SimulateView, `scores` scope on ScoreSubmissionView |
| Deploy pipeline ↔ RDS | `manage.py migrate` + `createcachetable` job | Runs before `sam deploy`; production settings + secrets |

---

## Build Order (dependency-driven phases)

The milestone items (HARD-01…08) cluster into six phases. Ordering rationale: fix the live RCE first; make the API testable; make the pipeline able to ship schema changes **before** adding new columns (token hash) and cache tables (throttles); then add the two schema-touching features.

1. **Phase A — Close the RCE (HARD-01 + HARD-06).** Finish/validate the working-tree fix: engine `_is_safe_equation` + `{"__builtins__": {}}` globals, form `float()`/`isfinite` + length caps, and the adversarial regression tests (inject `"; __import__('os')"`, oversized JSON, pathological formulas). *Addresses: Pattern 1. Avoids: Anti-Pattern 1.*
2. **Phase B — Fix broken endpoints (HARD-02, HARD-03, HARD-04).** `LevelProgressSerializer.model = LevelProgress`; explicit error dict from `BalanceChemicalReaction`; require both reactants and products. Small, independent, and a precondition for meaningful API tests. *Addresses: Pattern 4 prerequisites.*
3. **Phase C — API test coverage (HARD-05).** Per-endpoint suites + regression tests for Phases A/B (progress returns 200, forgery rejected once Phase D lands, RCE payloads fail). Write these as the guard that makes Phases D/E safe to change. *Addresses: Pattern 4.*
4. **Phase D — CI/CD deploy gates.** `makemigrations --check`, `check --deploy` on `ci.py` settings, blocking `pip-audit`, and a pre-deploy `migrate` + `createcachetable` step. **Must land before Phases E/F** — they add a column and a cache table that the pipeline must be able to apply. *Addresses: Pattern 5. Avoids: Anti-Pattern 5.*
5. **Phase E — Score integrity (HARD-07).** Consolidate scoring; `sign_result`/`verify_result`; `ScoreSubmissionView` recompute; `UserScore.result_token_hash` migration (now deployable thanks to Phase D). *Addresses: Pattern 2. Avoids: Anti-Pattern 2, 4.*
6. **Phase F — Rate limiting (HARD-08).** `DatabaseCache` + `NUM_PROXIES=1` + scoped throttles + WAF rate rule in `template.yaml` (the WAF rule can be shipped independently, but the app throttles need the cache table that Phase D creates). *Addresses: Pattern 3. Avoids: Anti-Pattern 3.*

**Why this order:** A (live RCE) and B (broken 500s) are the user-visible defects; C locks them in with tests; D is the enabler that makes every later schema/cache change deployable and every later regression CI-blocked; E and F are the two remaining hardening items and both depend on D. Nothing here touches the domain engines' physics (SCIENTIFIC_LOGIC.md stays inviolate) except removing the scoring duplication, which is pure refactor with golden-value tests.

**Research flags for phases:**
- Phase D: needs a decision on `pip-audit --ignore-vuln` allowlist policy and whether to fully pin numpy/scipy (loose ranges today make `--no-deps` impossible) — likely needs deeper research on current advisories.
- Phase E: replay-window vs unique-token-hash tradeoff should be confirmed against test expectations; the signing salt/key rotation story needs a decision (SECRET_KEY rotation invalidates outstanding tokens — acceptable).
- Phase F: WAF rule parameters (rate limit value, evaluation window) need load data; `NUM_PROXIES` correctness should be validated with a real CloudFront→Lambda request trace (log XFF in a staging test).

---

## Sources

- **chempy** — `Reaction.from_string` / `to_reaction` / `EqSystem.from_string` verified at `https://github.com/bjodah/chempy/blob/master/chempy/chemistry.py`, `chempy/util/parsing.py`, `chempy/equilibria.py`, `chempy/reactionsystem.py` (primary source; HIGH confidence). Context7 `/bjodah/chempy` (MEDIUM).
- **DRF throttling** — `https://github.com/encode/django-rest-framework/blob/master/rest_framework/throttling.py` and `/docs/api-guide/throttling.md` (NUM_PROXIES, X-Forwarded-For, cache usage; HIGH). Context7 `/encode/django-rest-framework` + `/websites/django-rest-framework` (MEDIUM).
- **Django DatabaseCache** — `https://github.com/django/django/blob/5.2/docs/topics/cache.txt` (createcachetable, culling-on-write, MAX_ENTRIES/CULL_PERCENTAGE; HIGH). Context7 `/django/django` (MEDIUM).
- **Django signing** — `https://github.com/django/django/blob/main/docs/topics/signing.txt` (dumps/TimestampSigner/BadSignature/SignatureExpired; HIGH). Context7 (MEDIUM).
- **Django CI gates** — `https://github.com/django/django/blob/main/docs/ref/django-admin.md` (makemigrations --check, migrate --check) and `docs/howto/deployment/checklist.txt` (check --deploy W-checks; HIGH). Context7 (MEDIUM).
- **pip-audit** — `https://github.com/pypa/pip-audit` README (exit codes 0/1, --ignore-vuln, --no-deps; HIGH).
- **AWS WAF rate-based rules** — `https://docs.aws.amazon.com/waf/latest/developerguide/` (rate limit range, evaluation window, IP aggregation, 10,000-IP managed-keys cap; HIGH via AWS official docs search).
- **Project internal** — `.planning/codebase/CONCERNS.md`, `.planning/codebase/ARCHITECTURE.md`, working-tree diff for HARD-01 (form + equilibria + tests), `config/settings/base.py`.

---
*Architecture research for: ChemicAlly hardening milestone (HARD-01…08)*
*Researched: 2026-08-05*
