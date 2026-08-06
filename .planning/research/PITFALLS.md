# Pitfalls Research

**Domain:** Django 5.2 + DRF chemistry web app hardening on AWS Lambda (serverless, session identity, no Redis)
**Researched:** 2026-08-05
**Confidence:** HIGH (codebase-grounded + empirically verified against chempy 0.9.0 source); MEDIUM for serverless-throttling claims (verified against DRF 3.15 docs, not live Lambda load)

## Critical Pitfalls

### Pitfall 1: Fixing the chempy `eval()` RCE with `globals_=False` alone — an incomplete fix that still executes code

**What goes wrong:**
The documented chempy fix — passing `rxn_parse_kwargs={"globals_": False}` to `EqSystem.from_string` — is **not sufficient** to close the RCE. chempy 0.9.0's `to_reaction` (`chempy/util/parsing.py:459`) calls `eval` in **two** places:
1. **Param eval** (line 504): `eval(param, globals_)` — correctly skipped when `globals_ is False` (`param = None`).
2. **Kwargs eval** (line 491): `kwargs.update(eval("dict(" + ";".join(parts[2:]) + ")", globals_ or {}))` — **runs on any line with 3+ semicolon-separated segments**. Because it uses `globals_ or {}`, passing `globals_=False` yields `{}`, and Python **auto-injects `__builtins__` into an empty globals dict**. Result: `A -> B; 1; x=__import__("os").system("touch /tmp/pwned")` **executes arbitrary code even with `globals_=False`**.

**Why it happens:**
The chempy docstring (and the CONCERNS.md recommendation) says `globals_=False` is "useful for web-apps," but it only guards the param path. The kwargs path is easy to miss without reading chempy source. Developers apply the documented fix, run the obvious regression test (`"; __import__('os')"` after the `;` K value), and ship — while the 3-segment payload stays exploitable.

**How to avoid:**
The current working-tree fix is the correct shape — keep it and extend the regression tests:
- Pass an explicit restricted globals dict, not `False`: `rxn_parse_kwargs={"globals_": {"__builtins__": {}}}` (empirically verified: blocks both the param and the kwargs eval paths).
- Enforce exactly-2-segment lines **before** chempy sees the string (`_is_safe_equation` already does: `len(parts) != 2` → reject). With 2-part enforcement, `parts[2:]` never exists, so the kwargs eval is unreachable even if globals handling regresses.
- Add a regression test (HARD-06) that sends **both** payload shapes through the full form→view→calculator chain:
  - param path: `{"reactants": "H2O", "products": "H+ + OH-", "k_mode": "Ka", "k_value": "__import__('os').system('touch /tmp/x')"}`
  - kwargs path: a reaction whose reconstructed equation has a 3rd segment, e.g. `k_value` that survives float() but injects `; x=__import__('os')` — assert 400 + no file created + no eval side effect.
- The definitive fix remains in the CONCERNS.md migration plan: build `EqSystem` from parsed dicts instead of string reconstruction, so chempy's eval is never reachable at all.

**Warning signs:**
- The fix diff touches only `globals_` and no line-count/segment validation.
- The regression test asserts "no `__import__` in K value" but the payload has only 2 segments — the kwargs path is untested.
- A comment says "`globals_=False` strips builtins" — that is false for the kwargs eval; the code comment at `equilibria.py:134-136` currently says the right thing, don't let it drift.

**Phase to address:**
HARD-01 (RCE fix) + HARD-06 (adversarial regression tests). The regression tests are the actual guarantee — the fix is only as good as the payload set.

---

### Pitfall 2: "Fixing" the serializer without an endpoint test — the `if not session_key` guard masks the bug

**What goes wrong:**
`LevelProgressSerializer` (`serializers.py:199-209`) declares `Meta.model = UserScore` with `LevelProgress`-only fields. DRF raises `ImproperlyConfigured` at **instantiation time**, so `GET /hplc/api/progress/` 500s — but only for requests that already have a session. The `if not session_key: return Response([])` guard at `views.py:258` short-circuits first-time visitors, so the endpoint "works" for anyone without a cookie. A fix that changes `model = LevelProgress` without adding an endpoint test will merge green and still be one import away from shipping broken — `LevelProgress` isn't even imported in `serializers.py` today.

**Why it happens:**
The bug shipped because zero tests instantiate the serializer through the view (see Pitfall 3). The serializers are also copied by pattern (`UserScoreSerializer` → `LevelProgressSerializer`), so a copy-paste defect is invisible until runtime. Fixes applied in isolation — "fix the Meta.model" — don't verify the view wiring (`LevelProgressView` imports and instantiates the right serializer with the right queryset).

**How to avoid:**
- Fix it as a unit: `model = LevelProgress` **plus** add `LevelProgress` to the `from .models import ...` line **plus** an endpoint test that (a) creates a session (`self.client.session` / login flow or `request.session.create()`), (b) submits a score via `/hplc/api/scores/` first, (c) asserts `GET /hplc/api/progress/` returns 200 with the expected fields.
- Add a serializer smoke test that instantiates **every** serializer in the file against a real model instance (a `test_serializers_instantiate` loop) so Meta mismatch / field drift fails at import/test-collection time, not in production.
- HARD-05's endpoint tests must run the **full request path** through `reverse()` — never test `LevelProgressSerializer(progress, many=True).data` in isolation as a substitute (see Pitfall 3).

**Warning signs:**
- The "fixed" serializer has no new test, or the new test hits `/hplc/api/progress/` without a session and asserts 200 `[]` — that passes trivially and exercises nothing.
- `LevelProgress` missing from the `serializers.py` import line (a lint-visible clue: the model is referenced only inside `Meta`).
- The endpoint test asserts status only, not the response body shape.

**Phase to address:**
HARD-02 (serializer fix) — but the prevention is HARD-05 (API test coverage). Order: fix + test in the same phase or HARD-05 immediately after; never merge HARD-02 without its endpoint test.

---

### Pitfall 3: API test coverage that doesn't exercise the endpoints — serializer/model tests masquerading as API tests

**What goes wrong:**
The existing `tests/hplc_simulator/test_validation.py` pattern — instantiating serializers directly and calling `is_valid()` — passes while the API is completely broken (this is exactly how the `LevelProgressSerializer` 500 shipped). If HARD-05's "API test coverage" repeats that pattern (test serializers, test `generate_chromatogram`, test model `clean()`), the milestone will claim "all six endpoints tested" while `/hplc/api/progress/` still 500s in production.

**Why it happens:**
Serializer-unit tests are fast, need no DB rows, and feel like API tests. They never catch: wrong serializer wired to a view, session-guard short-circuits, URL routing, `get_object_or_404` behavior, status codes, response serialization (the `SimulationResponseSerializer.is_valid(raise_exception=True)` contract at `views.py:158-159`), or exception-path behavior (`views.py:109-114`).

**How to avoid:**
HARD-05 must use **`APIClient` through `reverse()`** for all six endpoints (`api/levels/`, `api/levels/<slug>/`, `api/simulate/`, `api/scores/`, `api/scores/history/`, `api/progress/`):
- Use `rest_framework.test.APITestCase` (or pytest-django with `APIClient`) so `self.client` is the DRF client.
- Assert **status code AND response body shape** AND DB side effects (e.g., after POST `/api/scores/`, assert a `UserScore` row exists with the submitted session_key).
- For each endpoint, cover: happy path, validation-failure path (400), missing-resource path (404), and the session-guard path (no session → `[]`; with session → real data).
- Do **not** mock `generate_chromatogram` in these tests — the engine is pure and fast enough (5000 points); mocking it makes the response-serializer contract untested. If determinism is the concern, see Performance Trap (unseeded RNG).
- Add a coverage gate: run pytest with `--cov --cov-fail-under=80` on `apps/hplc_simulator/views.py` specifically — otherwise "coverage" silently decays.

**Warning signs:**
- New test files import only `serializers` / `models`, never `views` or `urls`.
- Tests use `APIRequestFactory` + calling `view.as_view()` directly instead of `self.client.get(reverse(...))` — that still bypasses URLconf and middleware.
- Coverage report shows `apps/hplc_simulator/views.py` at 0% after the "API test" phase.

**Phase to address:**
HARD-05. It is the highest-leverage phase — it would have caught HARD-02, and it anchors HARD-06/07/08 regression tests.

---

### Pitfall 4: "Server-side score recompute" that still trusts client-submitted metrics — anti-cheat theater

**What goes wrong:**
HARD-07's recommended approach in CONCERNS.md — "recompute score server-side from `min_resolution`/`total_run_time`/`max_pressure_bar`" — is **insecure if those three inputs are still the client-submitted values**. The attacker doesn't forge `score`; they forge `min_resolution` (or set `overpressure=True` to bypass the only cross-field check at `models.py:179-186`) and let the server "honestly" compute a top score. Recomputing from attacker-controlled inputs changes nothing.

**Why it happens:**
"Validate server-side" is the standard advice and sounds complete. But the score inputs here are *derived quantities* of a simulation the client already ran. The only trustworthy source is the server's own `SimulateView` computation — the client has nothing the server can verify against without re-running the simulation.

**How to avoid:**
Issue-and-verify signing (the strongest fix for this architecture):
1. `SimulateView` (already computes `score`/`is_successful` via `calculate_score` at `views.py:116-122`) returns a signed token alongside the response: `signing.dumps({"level_id": ..., "score": ..., "min_resolution": ..., "total_run_time": ..., "max_pressure_bar": ..., "overpressure": ...}, salt="hplc-simulate")`.
2. `ScoreSubmissionView` **ignores the client-supplied score/metrics fields** and takes `simulation_token`; `signing.loads(token, salt="hplc-simulate", max_age=300)` → `BadSignature` → 400.
3. Keep the `ScoreSubmissionSerializer` accepting the legacy fields for backward compat but stop persisting them as truth (or drop them — no released clients depend on them; the JS client is in this repo).
- **Prerequisite ordering trap:** signing uses `SECRET_KEY`. The hardcoded fallback key at `base.py:32-34` is committed to the repo — if score signing ships before that fallback is removed (and before production's `SECRET_KEY` is rotated/SSM-managed), anyone who can read the repo can forge tokens. **Remove the fallback SECRET_KEY (make `base.py` hard-fail like `production.py`) before or in the same phase as HARD-07.**
- Recompute-vs-sign decision: signing is cheaper (no re-run) and unifies the code path; recompute requires re-running `generate_chromatogram` server-side from the original config and is only viable if you also stop trusting client config. Choose signing.

**Warning signs:**
- The fix persists `data['score']` from the request body with only a `min_value=0` bound.
- The fix "recomputes" score but takes `min_resolution`/`total_run_time` from the same untrusted body.
- The `overpressure` flag remains client-controlled and still bypasses the pressure check.
- Signing ships while `base.py` still has the fallback key.

**Phase to address:**
HARD-07, sequenced after/with the SECRET_KEY fallback removal (currently a security consideration, not yet a HARD item — promote it). Note the existing `UserScore`/`LevelProgress` rows were all written under the forgeable scheme — plan a data note, not necessarily cleanup, since there are no accounts yet.

---

### Pitfall 5: DRF throttling on Lambda that throttles nothing — in-memory counters, per-container state, and shared proxy IPs

**What goes wrong:**
DRF's `SimpleRateThrottle` family stores counters in **Django's cache**, which defaults to `LocMemCache` — a per-process, in-memory dict. On Lambda with N concurrent containers:
- Each warm container has its **own** counter → the effective rate limit is roughly **N × configured rate** (an attacker just fans requests across containers).
- Cold starts **reset** counters → burst protection resets exactly when an attacker forces new containers.
- Behind CloudFront, `REMOTE_ADDR` is the **CloudFront edge IP** shared by all users → `AnonRateThrottle` buckets every user on the planet into one counter (either the limit is uselessly high, or one abusive user throttles everyone). DRF only reads `X-Forwarded-For` when `NUM_PROXIES` is configured, and even then `X-Forwarded-For` can be **client-spoofed** unless CloudFront is configured to overwrite it.

**Why it happens:**
DRF throttling *looks* like it works locally (single process, real IPs) and the docs say LocMemCache is "sufficient for basic setups." Serverless violates both assumptions invisibly — nothing errors, it just doesn't throttle.

**How to avoid:**
- **Best option for this stack (no Redis):** enforce rate limiting at the edge with a **CloudFront WAF rate-based rule** (e.g. 100 req/5min per IP on `/hplc/api/*`) — edge-level, survives cold starts, per-real-IP (CloudFront sets `X-Forwarded-For`). This matches the existing CloudFront setup and adds no runtime dependency. Do this in the throttling phase as the primary control.
- If in-app DRF throttling is also desired (defense in depth), configure it explicitly:
  - Set `DEFAULT_THROTTLE_CLASSES = [ScopedRateThrottle]` + `DEFAULT_THROTTLE_RATES = {"simulate": "...", "scores": "..."}` (currently `REST_FRAMEWORK` at `base.py:158-165` has **no** throttle config at all).
  - Add `throttle_scope` to **each** APIView — `ScopedRateThrottle` silently does nothing on views without it.
  - Understand it as a per-container brake, not a DoS defense; document the limitation.
- **Rate-limit the expensive + write-heavy endpoints separately:** `simulate` (CPU-bound, 5000-point EMG — the cost-abuse vector) and `scores` (unbounded `UserScore` row creation — the RDS bloat vector). Cheap GET endpoints (`levels`) need little.
- If a shared counter is ever needed, it requires Redis/ElastiCache or a DynamoDB TTL-based throttle backend — out of scope for "no Redis" but the only way to get true global throttling.

**Warning signs:**
- Throttle tests pass locally (single process) but the deployed API never returns 429 under burst load.
- `REST_FRAMEWORK` has no `DEFAULT_THROTTLE_CLASSES`/`DEFAULT_THROTTLE_RATES` after the phase (or has them but views lack `throttle_scope`).
- `NUM_PROXIES` unset while behind CloudFront.
- No WAF/edge control added — only in-app throttle config.

**Phase to address:**
HARD-08. Sequence after HARD-05 so throttling behavior (429 responses) is covered by endpoint tests.

---

### Pitfall 6: CI/CD gates that appear to block but swallow failures — `|| true`, dev-settings checks, and non-gating scans

**What goes wrong:**
The deploy pipeline has three silent-failure gates:
1. **Security scan is non-blocking:** `pip-audit ... || true` and `safety check ... || true` (`deploy.yml:97,100`) — a job that *always exits 0* even when critical vulnerabilities are found; results go to artifacts only. The `build` job `needs: [lint, test, security-scan]`, so it *looks* gated.
2. **`check --deploy` runs against development settings:** `DJANGO_SETTINGS_MODULE: config.settings.development` (`deploy.yml:15`) means `DEBUG=True`, so the production-only security checks (W018 DEBUG, W009 weak SECRET_KEY, session-cookie security, HSTS/SSL) **never fire**. The check is green *because* it's running in the wrong mode.
3. **Deploy verification swallows failures:** the verify step ends with `aws lambda get-function ... || echo "Function still updating..."` (`deploy.yml:226-230`) — a failed/partial deploy reports success.

**Why it happens:**
`|| true` is the classic "make the pipeline green" shortcut; it's comfortable because the scan output still appears in logs/artifacts, so it *feels* like the check happened. Dev-settings check is a copy-paste of the local dev environment into CI. Each is individually plausible and collectively invisible.

**How to avoid:**
- **Make the security scan gate:** drop `|| true`; use `pip-audit --fail-on=high` (fail on high/critical, warn on medium — exit code semantics documented) and `safety check --continue-on-error` only if a reviewable exception list exists. The pipeline's own `needs:` graph already wires the gate — it just never triggers.
- **Run `check --deploy` against production settings:** set `DJANGO_SETTINGS_MODULE: config.settings.production` and inject the required env vars (SECRET_KEY, DB vars, ALLOWED_HOSTS) in the test job — `production.py` hard-fails on missing env, which is exactly the test you want.
- **Make deploy verification real:** check `LastUpdateStatus` and fail the job on non-`Successful` (drop the `|| echo`).
- **Add the missing `manage.py migrate` step** to the deploy job (CONCERNS.md "Migrations never run"): today any new migration (e.g., future score-signing columns) ships to RDS-only... actually, never — nothing migrates production. This is a separate missing gate that will bite the HARD-07 changes if they add model fields.
- Harden against drift: fully pin requirements (CONCERNS.md notes DRF 3.17.1 installed vs 3.15.2 pinned) — a lockfile makes pip-audit results reproducible.

**Warning signs:**
- `grep -n "|| true" .github/workflows/deploy.yml` returns the scan lines.
- CI "passes" while `pip-audit` reports known-vulnerability hits in the artifacts.
- `check --deploy` output in CI shows W018/W009 absent even though production would flag them.
- A deploy lands with `LastUpdateStatus: Failed` and the workflow is green.

**Phase to address:**
A CI/CD hardening phase — either its own phase or the tail of the hardening milestone. Prefer its own phase: it's independent of the code fixes and has different verification (workflow-level, not pytest-level).

---

### Pitfall 7: Drifting duplicated validation — form-layer and engine-layer regexes that fall out of sync

**What goes wrong:**
The RCE defense now lives in **two places with two separate allowlists**: `forms.py:9` (`_SAFE_FORMULA_RE`) and `equilibria.py:18-21` (`_SAFE_FORMULA_RE` + `_SAFE_K_VALUE_RE` + `_is_safe_equation`). The form reconstructs the string (`forms.py:247`: `f"{reactants} = {products}; {k_expr}"` where `k_expr = f"10**-{k_value}"`), and the engine re-validates the reconstructed string. If a future change loosens one regex (e.g. "support scientific notation" adds `%` or `~` to the K regex, or a formula feature adds `,`) without the other, a validation bypass or a spurious rejection appears — and the failure mode is asymmetric (form accepts → engine rejects with the misleading "Unsafe or malformed reaction string").

**Why it happens:**
The same security check was duplicated (form layer for UX, engine layer for defense-in-depth) without a shared source of truth. `float(rxn["k_value"])` validation (`forms.py:213-222`) also accepts strings that the engine regex then rejects (e.g. `float("1_000")` → 1000.0 valid at form layer; `10**-1_000` fails `_SAFE_K_VALUE_RE` at engine layer) — a latent UX bug and a sign the layers disagree.

**How to avoid:**
- Extract the allowlist + `_is_safe_equation` into a single shared module (e.g. `calculations/safe_strings.py`) imported by both `forms.py` and `equilibria.py` — one definition, both layers use it.
- Better: stop reconstructing strings at all. Parse the JSON reaction dicts (`reactants`/`products`/`k_mode`/`k_value`) into chempy objects directly (`EqSystem` from substances + reactions, per the CONCERNS.md migration plan) so the form *is* the only parser and the engine never re-parses strings.
- Add the HARD-06 adversarial tests at the **engine layer** (direct `EquilibriaCalculator.calculate` calls with malicious strings) AND the **view layer** (full POST with the hidden JSON), so a drift in either layer fails CI.

**Warning signs:**
- The same regex literal appears in two files (grep for `_SAFE_FORMULA_RE`).
- A test passes the form but the calculator returns "Unsafe or malformed" for a legitimately-formatted input.
- New characters added to one allowlist but not the other.

**Phase to address:**
HARD-01/HARD-06, with the shared-module refactor done in the same phase (small, low-risk, eliminates the drift class).

---

### Pitfall 8: Throttling/security tests that are order-dependent or don't isolate state — flaky or vacuously-green CI

**What goes wrong:**
Two state-isolation traps in the new tests:
1. **Throttle tests share the cache:** DRF throttle counters live in Django's cache (LocMemCache). A test that posts 5 times and asserts 429 will **leak counters across tests** in the same process — test order changes pass/fail. Conversely, a test asserting "throttling works" can pass because a *previous* test already tripped the counter.
2. **Session-guard tests pass vacuously:** `UserScoresView`/`LevelProgressView` return `[]` when there's no session (`views.py:244, 258`). A test asserting `status_code == 200` against a fresh client exercises the guard, not the serializer.

**Why it happens:**
Django's test runner wraps each test in a DB transaction but **not** the cache (LocMemCache is module-level, persists across tests). New API tests copy the "assert 200" habit from the webapp tests, where the session guard wasn't a factor.

**How to avoid:**
- In throttle tests: `django.core.cache.cache.clear()` in `setUp`, and/or use `override_settings(DEFAULT_THROTTLE_RATES={...small rate...})` per test so behavior is deterministic regardless of order.
- In session-dependent tests: explicitly create the session (`session = self.client.session; session.save()` or drive `request.session.create()` via a real score POST) before hitting the history/progress endpoints; assert on **response body content**, not just 200.
- Use `APITestCase` with the `db` fixture/transaction so score-POST → progress-GET sequences share one session_key.

**Warning signs:**
- Throttle tests fail when run in a different order (`pytest-randomly` or `-p no:randomly` changes results).
- Progress/history tests assert 200 + `[]` only.
- Tests pass locally, fail in CI (cache state differs by suite ordering).

**Phase to address:**
HARD-05 + HARD-08. Write the state-isolation discipline into HARD-05's test conventions so HARD-08's throttle tests inherit it.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| `pip-audit ... \|\| true` / `safety ... \|\| true` in CI | Green pipeline while scanners run | Known-vulnerability deploys with a false sense of gate | Never — use `--fail-on` with a reviewable exception list |
| `check --deploy` against dev settings | No env-var plumbing in CI | Production-only security checks never run | Never — inject prod settings + env in CI |
| Keep the hardcoded fallback `SECRET_KEY` in `base.py` | Importing base.py standalone "just works" | Any signing/CSRF/session scheme is forgeable by repo readers | Never — mirror `production.py`'s `ImproperlyConfigured` |
| Client submits `score` + metrics, server persists | Trivial score API | Trivially forgeable leaderboards; RDS bloat | Only for a throwaway prototype, never for a shipped API |
| Test serializers/models in isolation as "API tests" | Fast, no DB rows needed | Broken endpoints ship with green suites (this codebase's live bug) | Never for endpoints — always add at least one full-path test per endpoint |
| DRF in-app throttling as the *only* rate control on Lambda | No infra added | Doesn't actually throttle across containers | Only as defense-in-depth under a WAF rate rule |
| `{"__builtins__": {}}` globals + regex as the permanent RCE fix | Ships today | Duplicated validation drifts; chempy upgrade may change eval paths | Acceptable short-term; migrate to parsed-dict `EqSystem` as the durable fix |
| Two scoring implementations (`engine.calculate_score` vs unused `scoring.evaluate_run`) | Existing code | Formula drift between server score and any future recompute path | Never — consolidate to one module before HARD-07 |
| `sam deploy --no-fail-on-empty-changeset` + `|| echo` verify | Deploys don't "fail" on no-op | Failed Lambda updates reported as success | Acceptable flag for no-op; never for the verify step |
| Session-key identity (no accounts) | No auth infra | Session fixation/forgery; scores tied to cookies that expire | Deliberate product decision (PROJECT.md Out of Scope) — but keep anti-forgery signing |

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Django cache (`LocMemCache`) with DRF throttling on Lambda | Assume throttling is global | Treat as per-container brake; put real rate limits in CloudFront WAF |
| CloudFront → Lambda → DRF IP detection | Use `AnonRateThrottle` with default `get_ident` | Configure `NUM_PROXIES` and ensure CloudFront overwrites `X-Forwarded-For`, or throttle at the edge |
| chempy 0.9.0 string parsing | Trust the `globals_=False` docstring | Read `chempy/util/parsing.py:459-530`; pass explicit restricted globals + enforce 2-segment lines; plan the parsed-dict migration |
| `django.core.signing` for score tokens | Sign with the committed fallback key | Remove `base.py:32-34` fallback first; use a dedicated salt (`salt="hplc-simulate"`) and `max_age` |
| RDS via Lambda (`CONN_MAX_AGE=0`) + throttling writes | Rate-limit only the CPU endpoint | Also scope-throttle `/api/scores/` — unbounded `UserScore` rows are the DB-bloat DoS |
| GitHub Actions + SAM | `--parameter-overrides` with `RDSPassword`/`DjangoSecretKey` in plaintext | Move secrets to SSM Parameter Store secure strings (CONCERNS.md recommendation) — also required before key rotation for signing |
| Session engine (default DB-backed) on Lambda | Ignore session table growth | Schedule `clearsessions`; cap `previous_substances` (CONCERNS.md) — interacts with score throttling volume |

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Per-container in-memory throttling | Burst load never returns 429; Lambda spend spikes on `/api/simulate/` | CloudFront WAF rate rule + in-app `ScopedRateThrottle` as backstop | ~>1 concurrent container under attack; effectively immediately in production |
| Unbounded `UserScore` rows per session | RDS bloat; slow `/api/scores/history/` filters | Cap history per session/level (keep top N); throttle `/api/scores/`; cleanup job | Steady abuse within days on a public endpoint |
| `np.random.normal` unseeded in `generate_chromatogram` (`engine.py:418`) | Non-deterministic simulation responses; golden-value API tests flaky | Accept a `seed` param; seed `np.random.default_rng(seed)` in engine and in tests | Flaky tests immediately; reproducible-simulation features impossible |
| Hidden JSON form fields at 5000-char `max_length` | Large session payloads; slow clean() | Cap by reaction count too; validate in JS and server-side consistently | At a few hundred reactions per submission |
| Session history `previous_substances` unbounded | Session table rows grow monotonically; O(n) append scan | Cap at last ~20; consider per-session table | Active users after weeks of use |

## Security Mistakes

Domain-specific security issues beyond general web security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Treating `globals_=False` as the complete chempy fix | RCE persists via the kwargs eval path (`parsing.py:491`) | Explicit `{"__builtins__": {}}` globals + 2-segment enforcement + kwargs-path regression test (empirically verified — this codebase's fix must not regress to `False`) |
| Trusting client-submitted `score`/`min_resolution`/`total_run_time`/`overpressure` | Score forgery; overpressure flag bypasses the only cross-field check | Sign simulation results in `SimulateView`; verify signature + `max_age` in `ScoreSubmissionView`; derive `overpressure` server-side |
| Signing tokens with the committed fallback `SECRET_KEY` | Any repo reader forges score tokens / session cookies | Remove `base.py:32-34` fallback before HARD-07; rotate the production key via SSM |
| `{{ result\|safe }}` on user-derived LaTeX (`reaction_balancer.html:30`) | XSS if chempy parsing is ever loosened | Escape the result or build LaTeX from structured tokens; add a regression test asserting the payload is escaped |
| `SimulateView` returning `{'detail': str(e)}` to anonymous callers | Internal paths/library detail disclosure | Log server-side; return a generic message (do in the same phase as HARD-05's exception-path tests) |
| Rate-limit by REMOTE_ADDR behind CloudFront without `NUM_PROXIES` | One counter for all users; spoofable XFF | Edge WAF rate rule; DRF `NUM_PROXIES` + CF XFF overwrite |
| `request.session.create()` on first score POST then unlimited writes | Session-key identity is a bearer cookie; unbounded rows | Throttle `/api/scores/`; consider signed token bound to session (with `max_age`) |

## UX Pitfalls

Common user experience mistakes in this domain.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| `/hplc/api/progress/` 500s only for returning users | Returning players see a broken page; first-time visitors "work" | HARD-02 fix + endpoint test with an established session (Pitfall 2) |
| Reaction-balancing silently renders "Could not balance" for missing reactants OR products | Users can't tell what they did wrong | HARD-04 (require both sides) + return a specific error message per failure mode |
| `EquilibriaCalculator` returns `str(e)` in the error dict on failure | Raw solver errors leak to the UI | Map known failure classes to friendly messages; log the real exception (HARD-01/06) |
| 429 responses with no client handling | Simulator JS fails on throttling without explanation | Handle 429 in the Alpine.js client: show "too many requests, wait N seconds" (Retry-After) |
| `float()` accepts strings the engine regex rejects (e.g. `1_000`) | Valid-looking K value produces "Unsafe or malformed" | Single shared validation module (Pitfall 7) |

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **RCE fix (HARD-01):** `globals_` passed as `False` instead of a restricted dict — verify the fix uses `{"__builtins__": {}}` and that `_is_safe_equation` enforces exactly-2 segments. Run the 3-segment kwargs payload (`; x=__import__('os')...`) through the full POST chain.
- [ ] **RCE regression tests (HARD-06):** payload set includes param-path AND kwargs-path AND non-2-segment lines, asserted at both engine and view layers, with a side-effect check (no file created / no marker written), not just a 4xx status.
- [ ] **Serializer fix (HARD-02):** `LevelProgress` added to the `serializers.py` import line; endpoint test runs WITH a session and AFTER a real score POST; response body asserted, not just 200.
- [ ] **API test coverage (HARD-05):** all six endpoints exercised via `APIClient` + `reverse()`; views.py coverage ≥ 80%; validation-failure and 404 paths asserted; no `generate_chromatogram` mocks.
- [ ] **Score validation (HARD-07):** persisted values derive from a server-signed token, not the request body; `overpressure` not client-controlled; `base.py` fallback `SECRET_KEY` removed first; tampered-token test asserts 400.
- [ ] **Throttling (HARD-08):** WAF rate rule exists (primary); in-app `ScopedRateThrottle` has `throttle_scope` on every APIView; `DEFAULT_THROTTLE_RATES` set; throttle tests clear the cache and assert a real 429 under burst.
- [ ] **CI/CD:** `grep -n "|| true"` on `deploy.yml` returns nothing; `check --deploy` runs with `config.settings.production` + env; `manage.py migrate` present in the deploy job; deploy verify fails on non-`Successful` `LastUpdateStatus`.
- [ ] **Secret handling:** no `SECRET_KEY` literal in `base.py`; `RDSPassword`/`DjangoSecretKey` moved to SSM.

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| RCE shipped (eval path unblocked) | HIGH — assume compromise | Rotate SECRET_KEY + RDS credentials immediately; review CloudWatch logs for `__import__`/`os.system`-style payloads; deploy the restricted-globals + 2-segment fix; consider a fresh deployment (image) to drop any running container state |
| Progress endpoint 500s in production | LOW | Ship HARD-02 + endpoint test; no data migration needed (read-only endpoint) |
| Forged scores already in DB | LOW | Note/flag rows; with no accounts there's no identity to preserve — optionally reset `UserScore`/`LevelProgress` after deploying HARD-07 |
| Throttle bypassed (Lambda cost spike) | MEDIUM | Deploy WAF rate rule (edge, immediate); verify 429 under load; scope-throttle `/api/scores/` |
| CI "green" while vulnerable deps ship | MEDIUM | Remove `\|\| true`, set `pip-audit --fail-on=high`; audit the artifact history for the gap window; pin/lockfile |
| Deploy reported success but Lambda failed | HIGH | Replace `\|\| echo` verify with status assertion; check `LastUpdateStatus`; roll back image tag to last known good (OPS_PLAYBOOK.md) |

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Incomplete `globals_=False` RCE fix (kwargs eval path) | HARD-01 + HARD-06 | Regression tests assert both payload shapes produce 400 + no side effect, through the full POST chain |
| Serializer fix without endpoint test; session guard masks 500 | HARD-02 (fix) + HARD-05 (tests) | `GET /hplc/api/progress/` returns 200 with body after a real score POST with an established session |
| API tests that don't exercise endpoints | HARD-05 | `APIClient` + `reverse()` for all 6 endpoints; `views.py` coverage ≥ 80%; each endpoint has happy/400/404/session-guard cases |
| Client-trusted score recompute (anti-cheat theater) | HARD-07 (sign) + SECRET_KEY-fallback removal phase | Tampered signed token → 400; persisted score equals server-computed value; `overpressure` not client-controlled |
| Per-container in-memory throttling / shared proxy IPs | HARD-08 | WAF rate rule in `template.yaml`; in-app scopes set; burst test (post 6+, expect 429) with cache cleared |
| `\|\| true` gates + dev-settings `check --deploy` + fake deploy verify | CI/CD hardening phase | `deploy.yml` has no `\|\| true`; check runs against production settings; job fails on non-`Successful` deploy; `manage.py migrate` present |
| Drifting duplicated validation regexes | HARD-01 (shared module refactor) | Single `_SAFE_*` definition imported by both layers; engine-layer + view-layer adversarial tests pass |
| Cache/session state leaking across tests | HARD-05 conventions → HARD-08 | Throttle tests pass under `pytest-randomly`; session tests assert body content |

## Sources

- **chempy 0.9.0 source** (`chempy/util/parsing.py:459-530`, `to_reaction`; `chempy/equilibria.py:371-402`, `EqSystem.from_string`): read directly from the pinned sdist; eval behavior **empirically verified** against the project venv (created a file via the kwargs-eval path with `globals_=False`; confirmed `{"__builtins__": {}}` blocks both paths). Confidence: HIGH.
- **DRF 3.15 official docs** (throttling, testing, exceptions, settings) via Context7: `LocMemCache` default, `NUM_PROXIES`/`get_ident`, `ScopedRateThrottle` scope semantics, `APITestCase`/`APIClient`, `EXCEPTION_HANDLER`. Confidence: HIGH for mechanics, MEDIUM for Lambda-scale implications (not load-tested).
- **Django 5.2 official docs** (`check --deploy` security tags W009/W018/W020; `django.core.signing` `dumps`/`loads`/`max_age`) via Context7. Confidence: HIGH.
- **Codebase audit** (`.planning/codebase/CONCERNS.md`, `.planning/codebase/ARCHITECTURE.md`), `deploy.yml`, `forms.py`, `equilibria.py`, `serializers.py`, `views.py`, `models.py`, `base.py`, existing tests: all read directly. Confidence: HIGH.
- **OWASP API Security Top 10** (general): rate-limit/abuse and broken object-level auth principles applied to this app's public Function URL. Confidence: MEDIUM (not re-verified live).
- **Serverless throttling behavior**: DRF docs + AWS Lambda container model — each warm container is an isolated process with its own in-memory cache. Confidence: MEDIUM (architecture-derived, standard serverless knowledge; not load-tested on this deployment).

**Research gaps:**
- No live Lambda load test was run — the "N × rate" throttle bypass and CloudFront REMOTE_ADDR behavior should be verified empirically during HARD-08 (add a short burst test in the phase).
- `pip-audit --fail-on` exit-code semantics should be re-checked against the pinned `pip-audit` version at CI-hardening time.
- The exact CloudFront configuration in `template.yaml` (whether it forwards/overwrites `X-Forwarded-For`) was not inspected in depth — verify before relying on DRF-side IP throttling.

---
*Pitfalls research for: ChemicAlly hardening milestone (RCE fix, serializer fix, API tests, throttling, score validation, CI/CD)*
*Researched: 2026-08-05*
