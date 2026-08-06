# Project Research Summary

**Project:** ChemicAlly — security hardening milestone (HARD-01…08)
**Domain:** Production-hardened Django 5.2 + DRF chemistry web app (calculators + HPLC simulator API) on AWS Lambda (Mangum, RDS, no Redis/Celery, session-only identity, public Function URL)
**Researched:** 2026-08-05
**Confidence:** HIGH (chempy/DRF/Django mechanics verified against source; live PyPI versions; AWS WAF docs). MEDIUM for serverless throttling behavior under real load.

## Executive Summary

ChemicAlly is a live, publicly-exposed Django 5.2 + DRF chemistry app with a **critical unauthenticated RCE** (chempy `eval()` in the equilibria calculator), a broken public endpoint (`/hplc/api/progress/` 500s), forgeable game scores, zero API test coverage, no rate limiting, and CI gates that silently pass. The research confirms the hardening milestone is correctly scoped: this is a *security-and-verifiability* milestone, not a feature milestone, and every item in HARD-01…08 maps to an exploit that is live today or a gate that is theater. The app must be made safe and *provably* safe — fixes without regression tests and CI gates that block are not trusted.

The recommended approach, verified against chempy 0.9.0 source, is a **layered defense**: (1) close the RCE with a restricted eval globals dict `{"__builtins__": {}}` + exactly-two-segment reaction enforcement + numeric-only K validation — **not** the `globals_=False` that CONCERNS.md recommended, because chempy's kwargs-eval path (`eval("dict(" + ";".join(parts[2:]) + ")", globals_ or {})`) auto-injects builtins into an empty dict and `globals_=False` silently nulls the equilibrium constant (the working tree's fix is strictly stronger — do not regress it); (2) sign simulation results with Django `TimestampSigner` at `SimulateView` and verify at `ScoreSubmissionView`, sequenced after removing the committed `SECRET_KEY` fallback; (3) two-layer rate limiting — CloudFront WAF rate rule as the enforcement boundary, DRF throttles backed by `DatabaseCache` on RDS as policy/backstop (LocMemCache is per-container on Lambda and every user collapses into one edge-IP bucket behind CloudFront); (4) an independent CI/CD hardening workstream — blocking `pip-audit`, `check --deploy` against production settings via a new `ci.py`, `migrate` + `createcachetable` before `sam deploy`, and a deploy-verify step that actually fails on failed updates.

The key risk is not the individual fixes (all are small and well-documented) but **ordering and trust**: the working-tree RCE fix must not regress to `globals_=False`, API test coverage (HARD-05) and adversarial RCE regression tests (HARD-06) are prerequisites for trusting any other change, and the pipeline must be able to ship schema changes (token-hash column, cache table) *before* the schema-touching phases (HARD-07/08) land. The build order below sequences exactly that way. Mitigation for every identified pitfall is concrete and code-grounded, with empirical verification of the critical RCE claim.

## Key Findings

### Recommended Stack

Stay on **Django 5.2 LTS** (bump 5.2.3 → 5.2.17, 14 patch releases of fixes; do NOT jump to 6.x mid-hardening) and **DRF 3.17.2** (pin exactly to stop the observed venv/CI drift, currently 3.15.2 pinned vs 3.17.1 installed). Keep pytest 8.4.0 + pytest-django 4.13.0, flake8 (no ruff churn this milestone), chempy 0.9.0 (no bump mid-hardening; verify 0.9.0's `EqSystem.from_string` accepts `rxn_parse_kwargs` at implementation time), and numpy/scipy ranges (engine uses `np.trapezoid`). **Drop safety 3.x** — verified paywalled ($25/seat/mo, account-gated, single-user free tier) — in favor of free PyPA **pip-audit 2.10.1** (exit codes 0/1, non-suppressible). Add **bandit 1.9.4** (would have caught the `eval` at B307) and **coverage.py + pytest-cov** for a measurable API-test gate. No new runtime dependencies: signing is Django stdlib, throttling is DRF built-in, cache is Django `DatabaseCache` on existing RDS.

**Core technologies:**
- Django 5.2.17: web framework/security surface — LTS to 2028; stay on line, absorb 14 patch releases
- DRF 3.17.2: HPLC API + throttling — pin exactly; 3.15→3.17 includes throttling behavior this milestone depends on
- pytest-django 4.13.0 + APIClient: API test coverage (HARD-05/06) — the trust anchor for every fix
- bandit 1.9.4: static security lint — B307 catches `eval`-adjacent regressions in CI
- pip-audit 2.10.1: blocking dependency gate — replaces paywalled safety; exit 1 blocks deploy
- AWS WAF v2 rate-based rules: the only true DoS enforcement boundary — blocks before Lambda compute cost
- coverage.py + pytest-cov: `--cov-fail-under` gate — makes "API test coverage" measurable and CI-enforced
- `django.core.signing` (TimestampSigner) + DRF `ScopedRateThrottle` + `DatabaseCache`: score anti-forgery and throttling with zero new dependencies

### Expected Features

This milestone is five hardening areas, not user features: (a) parser-boundary security, (b) DRF API robustness, (c) API test coverage, (d) score integrity, (e) CI/CD security gates. The app is live on a public Function URL — every "must have" below is an exploitable or broken behavior *today*.

**Must have (table stakes):**
- Close the chempy eval RCE with restricted globals + 2-segment enforcement + numeric K validation (HARD-01) — live RCE, nothing ships before it
- Adversarial RCE regression tests covering both eval paths (param AND kwargs) with side-effect assertions (HARD-06) — the fix is only as good as its payload set
- API test coverage for all six `/hplc/api/*` endpoints via `APIClient` + `reverse()` (HARD-05) — zero view tests shipped the broken serializer
- Fix `LevelProgressSerializer` (HARD-02) — 500s for every returning user; prerequisite to HARD-05
- Reaction-balancing explicit errors + both-sides validation (HARD-03/04)
- Server-authoritative scoring — signed result tokens, never client `score`/`min_resolution`/`overpressure` (HARD-07)
- DRF throttling on `simulate` + `scores` scopes (HARD-08) — CPU-cost and RDS-bloat abuse vectors
- `check --deploy` against production settings + blocking `pip-audit` in CI — make the gates actually gate
- No internal exception detail in API responses (`str(e)` leak in SimulateView)

**Should have (competitive):**
- Deterministic seeded simulation engine (`np.random.default_rng(seed)`) — unlocks golden-value regression tests and reproducible results
- Golden-value tests bound to `SCIENTIFIC_LOGIC.md` invariants — locks the physics with tolerance
- Shared validation module (`calculations/security.py`) — kills the form/engine regex drift class
- Coverage gate per app — keeps the API suite honest after it lands
- SSM Parameter Store for secrets + WAF rate rule at CloudFront (P2/P3 respectively)

**Defer (v2+):**
- User accounts / leaderboards — the honest fix for cross-session cheating, but explicitly out of scope (PROJECT.md); signing + recompute is the minimal anti-forgery fix
- RNG seeding + golden tests — after the HARD-05/06 pattern is established
- Error monitoring (Sentry/CloudWatch 5XX alarms) — after the milestone proves stable
- RDS Proxy / PgBouncer — only at scale; history caps + `clearsessions` first
- Redis-backed throttling — triggers only when real traffic makes DB-write amplification measurable

### Architecture Approach

Keep the two-app monolith and existing boundaries; hardening adds files, not rewrites. The architecture is a set of independent defense layers around an eval-based parser and a trustless score API: **edge layer** (CloudFront + WAF rate rule, blocks before Lambda), **HTTP boundary** (form/serializer validation gates that build chempy strings in exactly one place), **engine boundary** (re-validates strings with eval locked to no-builtins even when the form is bypassed), **score-integrity layer** (SimulateView signs results, ScoreSubmissionView verifies + recomputes from signed metrics), **throttle layer** (DRF scoped throttles on shared `DatabaseCache`), and **CI/CD gates** (migrations + security checks block deploys). Key verified mechanics: `DatabaseCache` on existing RDS gives shared, durable throttle state without Redis; `NUM_PROXIES = 1` makes DRF read the real viewer IP from CloudFront's XFF; the deploy pipeline must run `migrate` + `createcachetable` before `sam deploy` so schema-touching phases can ship.

**Major components:**
1. Input sanitization gates (forms + serializers) — reject malicious input at HTTP boundary; build chempy strings in ONE place; share regexes via `calculations/security.py`
2. Engine boundary (`_is_safe_equation` + `EqSystem.from_string(rxn_parse_kwargs={"globals_": {"__builtins__": {}}})`) — last line of defense before chempy; stays safe for all callers
3. Scoring module + result signer/verifier (`hplc_simulator/security/results.py`) — single source of truth for score math; `sign_result`/`verify_result` with `max_age` and anti-replay `result_token_hash`
4. Throttle layer (`hplc_simulator/security/throttles.py` + `DatabaseCache`) — session-keyed scoped throttles; per-container approximation documented
5. CI/CD gates (`ci.py` settings, blocking pip-audit, `makemigrations --check`, pre-deploy `migrate`/`createcachetable`, real deploy-verify) — nothing deploys un-migrated, vulnerable, or security-flagged
6. Endpoint-mirrored test suites (`tests/hplc_simulator/api/*` per endpoint + security suite) — one file per URL, happy/400/404/session-guard cases each

### Critical Pitfalls

1. **`globals_=False` is an incomplete RCE fix** — chempy's `to_reaction` calls `eval` in *two* places; the kwargs path (`eval("dict(" + ";".join(parts[2:]) + ")", globals_ or {})`) runs on any 3-segment line and auto-injects builtins into `{}`, so `globals_=False` still executes `__import__('os')` payloads AND silently nulls the K value. Avoid: keep the working tree's `{"__builtins__": {}}` dict + exactly-2-segment `_is_safe_equation` enforcement + regression tests for **both** payload shapes with side-effect assertions.
2. **API tests that don't exercise endpoints** — testing serializers/models in isolation is exactly how the `LevelProgressSerializer` 500 shipped. Avoid: `APITestCase` + `APIClient` through `reverse()` for all six endpoints; assert status AND body AND DB side effects; never mock `generate_chromatogram`; a session-guard "200 + []" assertion is vacuous — create the session first.
3. **"Server-side recompute" that still trusts client metrics is anti-cheat theater** — recomputing from attacker-supplied `min_resolution`/`overpressure` changes nothing. Avoid: sign simulation results at SimulateView, verify + recompute from *signed* metrics at ScoreSubmissionView, ignore client numerics. Prerequisite trap: signing with the committed fallback `SECRET_KEY` is forgeable — remove `base.py`'s fallback (make it `ImproperlyConfigured` like production) before or with HARD-07.
4. **DRF throttling on Lambda that throttles nothing** — LocMemCache counters are per-container (limit ≈ N × configured), cold starts reset them, and `REMOTE_ADDR` behind CloudFront is a shared edge IP. Avoid: WAF rate rule as primary enforcement (edge, survives cold starts, real per-IP); in-app `ScopedRateThrottle` with `throttle_scope` on every APIView as documented backstop; `NUM_PROXIES=1`; treat in-app limits as per-container brakes, not DoS defense.
5. **CI gates that appear to block but swallow failures** — `pip-audit ... || true`, `check --deploy` against dev settings (skips every deploy-only check), and `|| echo` deploy-verify all report success on failure. Avoid: drop `|| true`, run checks against production settings (new `ci.py`), assert `LastUpdateStatus == Successful`, add the missing `manage.py migrate` step — nothing migrates production today.
6. **Drifting duplicated validation regexes** — form and engine each carry `_SAFE_FORMULA_RE`; one loosens, the other holds, and failures are asymmetric. Avoid: extract shared regexes into one module imported by both layers in the HARD-01 phase; add engine-layer AND view-layer adversarial tests.

## Implications for Roadmap

Based on combined research, the HARD-01…08 items cluster into **six phases**. Ordering logic: fix the live RCE and the broken endpoints first (user-visible exploits), lock them in with tests, make the pipeline able to ship schema changes **before** adding the schema-touching features (token-hash column, cache table), then land the remaining two hardening items. CI/CD hardening is its own phase — it is independent of the code fixes and has workflow-level (not pytest-level) verification.

### Phase 1: Close the RCE (HARD-01 + HARD-06)
**Rationale:** Live unauthenticated RCE on a public Function URL — nothing else ships before this. The working-tree fix (restricted globals dict + 2-segment enforcement + numeric K regex) is verified stronger than the original `globals_=False` recommendation; validate it, extract the shared validation module, and build the regression guard in the same phase.
**Delivers:** Safe chempy parsing; `calculations/security.py` shared regexes; adversarial tests for both eval paths (param + kwargs) and non-2-segment lines, asserted at engine AND view layers with side-effect checks (no file/marker written), not just 4xx.
**Addresses:** Parser-boundary eval elimination, numeric validation, length caps, adversarial regression tests (table stakes from FEATURES.md).
**Avoids:** Pitfall 1 (`globals_=False` incomplete fix), Pitfall 7 (regex drift — shared module refactor here).

### Phase 2: Fix Broken Endpoints (HARD-02, HARD-03, HARD-04)
**Rationale:** `LevelProgressSerializer` 500s for every returning user; reaction-balancing fails cryptically. Small, independent, and a precondition for meaningful API tests. Fix the serializer as a *unit*: `model = LevelProgress` + the missing `LevelProgress` import + a serializer smoke test that instantiates every serializer.
**Delivers:** Working `/hplc/api/progress/`; explicit error results from reaction balancing; both-sides validation with per-failure-mode messages.
**Addresses:** LevelProgressSerializer fix, reaction-balancing error handling (table stakes).
**Avoids:** Pitfall 2 (serializer fix without endpoint test — the test lands in Phase 3 but the fix must be designed for it; never merge HARD-02 without its endpoint test).

### Phase 3: API Test Coverage (HARD-05)
**Rationale:** The trust anchor. Zero view tests shipped the serializer bug and the forgeable score endpoint; every later phase (E/F) changes security-critical code that needs regression tests in place first. Must use `APIClient` through `reverse()` with session establishment, body-shape assertions, and DB side-effect checks; disable throttling in tests via `override_settings` so the suite isn't self-429ing.
**Delivers:** Per-endpoint suites for all six `/hplc/api/*` endpoints (happy/400/404/session-guard), the progress-500 regression test, the RCE payload tripwires, views.py coverage gate.
**Addresses:** API test coverage (table stakes) + coverage gate (differentiator).
**Avoids:** Pitfall 3 (non-endpoint tests masquerading as API tests), Pitfall 8 (cache/session state leaking across tests — write the isolation discipline here so Phase 6 inherits it).

### Phase 4: CI/CD Deploy Gates
**Rationale:** Must land before Phases 5–6 — they add a column (`result_token_hash`) and a cache table that the pipeline must be able to apply. Today nothing migrates production, the security scan never blocks, `check --deploy` runs against dev settings, and a failed deploy reports success. Independent workstream with workflow-level verification.
**Delivers:** Blocking `pip-audit` (drop `|| true`; `--ignore-vuln` allowlist for accepted advisories); `check --deploy` against production settings via new `config/settings/ci.py` (imports production + dummy env); `makemigrations --check`; pre-deploy `migrate` + `createcachetable`; deploy-verify that fails on non-`Successful` `LastUpdateStatus`; optionally move `RDSPassword`/`DjangoSecretKey` to SSM.
**Uses:** pip-audit 2.10.1, bandit 1.9.4, coverage gate, `ci.py` settings module.
**Implements:** CI/CD gates component (Pattern 5); enabler for score-integrity and rate-limit phases.
**Avoids:** Pitfall 6 (`|| true` gates, dev-settings checks, fake deploy verify).

### Phase 5: Score Integrity (HARD-07)
**Rationale:** Requires Phase 4 (migration deployability) and the SECRET_KEY fallback removal — signing with a committed key is forgeable by any repo reader. The engine must return raw metrics and a single scoring module must own score math (today `scoring.py` is an unused duplicate of `calculate_score`). Pure server recompute from client metrics is insufficient — sign instead.
**Delivers:** `sign_result`/`verify_result` (TimestampSigner, `max_age` ~30 min, salt `hplc-result`); SimulateView issues tokens; ScoreSubmissionView verifies, recomputes from *signed* metrics, ignores client numerics; `UserScore.result_token_hash` unique column (anti-replay); consolidated scoring module; tampered-token and replay tests → 400.
**Addresses:** Server-authoritative scoring, signed result tokens (table stakes + differentiator).
**Avoids:** Pitfall 4 (client-trusted recompute theater; SECRET_KEY sequencing trap); Anti-Patterns 2 and 4 (client metrics, duplicated scoring).

### Phase 6: Rate Limiting (HARD-08)
**Rationale:** Requires Phase 4's `createcachetable` for the shared throttle cache. Two-layer by design: CloudFront WAF rate-based rule (primary enforcement; ~2000/300s on `/hplc/api/simulate/`, ~5000/300s global; scope to `/hplc/api/*` + `/equilibria`) + DRF `ScopedRateThrottle` backstop on shared `DatabaseCache` with `NUM_PROXIES=1`. Also close the direct-Function-URL WAF bypass (custom header check for CloudFront-originated traffic; flag API Gateway migration as infra follow-up). Throttle the expensive (`simulate`) AND write-heavy (`scores`) endpoints separately; burst test asserts real 429.
**Delivers:** WAF rule in `template.yaml`; `DatabaseCache` + `NUM_PROXIES=1`; scoped throttles on every APIView; throttle regression test (N+1 calls → 429, cache cleared); Function URL origin-restriction.
**Addresses:** DRF throttling (table stakes) + WAF rate rules (P3 deferred item pulled in as the enforcement boundary).
**Avoids:** Pitfall 5 (per-container in-memory throttling, shared edge-IP bucket); Anti-Pattern 3.

### Phase Ordering Rationale

- **RCE → broken endpoints → tests → pipeline → schema features:** A and B are the user-visible exploits; C locks them in; D is the enabler that makes every later schema/cache change deployable and CI-blocked; E and F both depend on D (token-hash migration, cache table).
- **Tests before trust:** HARD-05/06 are prerequisites for trusting ANY fix — the serializer bug and score forgery shipped precisely because nothing exercised the views.
- **CI/CD as its own workstream:** independent of code fixes, different verification tier (workflow-level vs pytest-level), and a hard prerequisite for the two schema-touching phases.
- **Do not regress the RCE fix:** `globals_=False` must never reappear; the restricted-dict + 2-segment pattern is verified strictly stronger — treat any diff that reverts it as a blocker.
- **No physics risk:** nothing here touches the domain engines' invariants (SCIENTIFIC_LOGIC.md) except removing the scoring duplication, which is pure refactor with golden-value tests.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4:** `pip-audit --ignore-vuln` allowlist policy and whether to fully pin numpy/scipy (loose ranges today make `--no-deps` impossible); re-check `pip-audit --fail-on` exit-code semantics against the pinned version.
- **Phase 5:** Confirm replay-window vs unique-token-hash tradeoff against test expectations; decide SECRET_KEY rotation story (rotation invalidates outstanding tokens — acceptable, document it).
- **Phase 6:** WAF rule parameters (rate value, evaluation window) need load data; validate `NUM_PROXIES` correctness with a real CloudFront→Lambda request trace (log XFF in staging); inspect current `template.yaml` CloudFront XFF forwarding config; empirically verify the "N × rate" throttle bypass with a short burst test; decide the direct-Function-URL fix (custom header vs API Gateway migration).

Phases with standard patterns (skip research-phase):
- **Phase 1:** RCE mechanics empirically verified against chempy 0.9.0 source; working-tree fix already implemented — implementation-time verification (0.9.0 kwargs acceptance) is a one-line REPL check, not a research phase.
- **Phase 2:** Trivial, well-understood serializer/model fixes with standard test patterns.
- **Phase 3:** DRF testing is thoroughly documented (APITestCase/APIClient patterns verified); no research-phase needed.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions verified against live PyPI data (2026-08-05); safety paywall, pip-audit exit codes, DRF/Django APIs verified against official docs/source; WAF behavior via AWS docs |
| Features | HIGH | Library behaviors verified against docs/source; OWASP taxonomy applied; LOW only on competitor market specifics (no external search — frame is the documented Django/DRF baseline, not a gap) |
| Architecture | MEDIUM-HIGH | Every mechanics claim verified against primary sources (chempy/DRF/Django source, AWS WAF docs); design synthesis (component boundaries, build order) is researcher recommendation |
| Pitfalls | HIGH | Codebase-grounded; chempy eval behavior **empirically verified** against the venv (kwargs-eval path created a file under `globals_=False`; restricted dict blocks both paths); MEDIUM on serverless throttling (architecture-derived, not load-tested) |

**Overall confidence:** HIGH for the security mechanics (the RCE claim is empirically verified, which is the load-bearing finding); MEDIUM for Lambda-scale behavior that can only be confirmed by load/staging tests during Phases 4–6.

### Gaps to Address

- **Live Lambda load behavior** (Phase 6): the "N × rate" throttle bypass and CloudFront XFF/REMOTE_ADDR behavior were not load-tested; add a short burst test during the phase and validate `NUM_PROXIES` with a real request trace.
- **chempy 0.9.0 kwargs acceptance** (Phase 1): `EqSystem.from_string(rxn_parse_kwargs=...)` verified on master; confirm the pinned 0.9.0 signature at implementation (one-line REPL check).
- **CloudFront XFF forwarding** (Phase 6): whether `template.yaml`'s CloudFront overwrites `X-Forwarded-For` was not inspected in depth — verify before relying on DRF-side IP identity.
- **pip-audit `--fail-on` semantics**: re-check against the pinned version at CI-hardening time.
- **pip-audit allowlist policy** (Phase 4): blocking scans can stall deploys on unfixable advisories — decide the `--ignore-vuln` review workflow before the gate goes live.
- **Data note for forged scores** (Phase 5): existing `UserScore`/`LevelProgress` rows were written under the forgeable scheme; no accounts exist so no cleanup is needed, but plan a data note.

## Sources

### Primary (HIGH confidence)
- chempy source (`chempy/chemistry.py`, `chempy/util/parsing.py:459-530`, `chempy/equilibria.py`) — `to_reaction` eval paths, `globals_` semantics, empirically verified against the project venv (0.9.0 sdist)
- PyPI JSON API (live versions 2026-08-05) — Django 5.2.17, DRF 3.17.2, pytest-django 4.13.0, bandit 1.9.4, pip-audit 2.10.1, safety 3.8.1, coverage 7.15.3, pytest-cov 7.1.0, chempy 0.10.1 vs pinned 0.9.0
- safety PyPI page — commercial licensing/account model (paywalled)
- DRF source/docs (throttling, testing, exceptions, settings) — NUM_PROXIES/XFF, ScopedRateThrottle, APIClient, EXCEPTION_HANDLER
- Django 5.2 docs/source — `check --deploy` W-checks, `django.core.signing` (TimestampSigner, dumps/loads, max_age), DatabaseCache/createcachetable, `makemigrations --check`
- AWS WAF rate-based rule docs — windows, min rate limit, IP aggregation
- pip-audit README — exit codes, `--ignore-vuln`, GH Action
- Project ground truth — CONCERNS.md, PROJECT.md, deploy.yml, working-tree RCE fix, codebase files

### Secondary (MEDIUM confidence)
- Context7 (`/encode/django-rest-framework`, `/django/django`, `/bjodah/chempy`) — docs digests corroborating primary sources
- OWASP API Security Top 10 2023 — risk taxonomy (API3/API4/API6/API8)
- GitHub Actions security hardening docs — CI/CD best practices

### Tertiary (LOW confidence)
- Competitor feature analysis — no external search possible this run; frame is the documented baseline for chemistry tools + production Django/DRF APIs
- Serverless throttling scale implications — architecture-derived from DRF docs + Lambda container model; not load-tested

---
*Research completed: 2026-08-05*
*Ready for roadmap: yes*
