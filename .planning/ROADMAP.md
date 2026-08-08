# Roadmap: ChemicAlly — Security Hardening

## Overview

ChemicAlly is a live Django 5.2 + DRF chemistry app on a public AWS Lambda Function URL. The audit surfaced a critical unauthenticated RCE (chempy `eval()`), a broken `/hplc/api/progress/` endpoint, forgeable simulation scores, zero API test coverage, no rate limiting, and CI gates that silently pass. This milestone makes the app safe and *provably* safe — six phases ordered by exploit severity and deployment dependency: close the live RCE, fix the broken endpoints, lock everything in with endpoint-level tests, make CI/CD actually gate and able to ship schema changes, then land the two schema-touching hardening items (score integrity, rate limiting).

**Coverage:** all 30 v1 requirements mapped to exactly one phase. No orphans.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Close the RCE** - Gate chempy parsing behind a shared no-builtins validation boundary; lock it in with adversarial regression tests (completed 2026-08-08)
- [ ] **Phase 2: Fix Broken Endpoints** - Repair the progress endpoint 500, reaction-balancing silent failures, and exception-detail leaks
- [ ] **Phase 3: API Test Coverage** - Endpoint-level tests for all six `/hplc/api/*` endpoints with a self-enforcing coverage gate
- [ ] **Phase 4: CI/CD Deploy Gates** - Make security scans, deploy checks, and schema migration genuinely block the pipeline
- [ ] **Phase 5: Score Integrity** - Server-authoritative signed scores; no forgeable or replayable results
- [ ] **Phase 6: Rate Limiting** - Two-layer throttling (CloudFront WAF + DRF backstop) on public endpoints

## Phase Details

### Phase 1: Close the RCE

**Goal**: The public equilibria calculator safely rejects untrusted input — chempy parsing is gated by a shared, no-builtins validation boundary that cannot execute attacker code.
**Mode**: mvp
**Depends on**: Nothing (first phase — live unauthenticated RCE ships ahead of nothing else)
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-04, SEC-05, SEC-06, TEST-03
**Success Criteria** (what must be TRUE):

  1. A user submitting a valid equilibria query (`formula = formula; K`) receives the correct computed result — legitimate chemistry is unchanged.
  2. A user submitting any reaction string that is not exactly `formula = formula; K` (extra segments, non-numeric K, oversized hidden `reactions`/`concentrations` fields) receives a clean validation error — the input is never evaluated.
  3. Adversarial payloads targeting both chempy eval paths (param and kwargs shapes) fail with no server side effects (no files/markers written) — asserted by regression tests at both engine and view layers.
  4. Form and engine validation draw from a single shared module (`calculations/security.py`) — validation cannot drift between layers.

**Plans**: 3/3 plans executed

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Shared no-builtins validation boundary (security.py extraction, engine wiring, SEC-02 pin, engine error contract, predicate matrix)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Form/view layers join the boundary (shared regex + drift guard, generic JSON copy, kwargs view tripwire, a11y)

**Wave 3** *(gap closure — CR-01 + WR-01..04 from 01-VERIFICATION.md)*

- [x] 01-03-PLAN.md — Gap closure: unknown-unit 500 guard, engine error classification, exact rejection contract (\Z anchors + finite-K gate), form/engine behavioral equivalence

**UI hint**: yes

### Phase 2: Fix Broken Endpoints

**Goal**: Broken and cryptic endpoint behavior is repaired: the progress endpoint works, reaction balancing fails loudly and explicitly, and error responses never leak internals.
**Mode**: mvp
**Depends on**: Phase 1
**Requirements**: API-01, API-02, API-03, API-04
**Success Criteria** (what must be TRUE):

  1. A returning-session user can `GET /hplc/api/progress/` and receives 200 with their `LevelProgress` data — no more 500s.
  2. A user submitting an unbalanced/impossible reaction to the balancing calculator receives an explicit, human-readable error result instead of a blank or cryptic failure.
  3. A user submitting a reaction with only reactants or only products receives a validation error requiring both sides.
  4. API error responses (e.g. from `SimulateView`) never include internal exception details — safe, generic error bodies only.

**Plans**: TBD
**UI hint**: yes

### Phase 3: API Test Coverage

**Goal**: All six `/hplc/api/*` endpoints are covered by endpoint-level tests through `APIClient` + `reverse()`, and a coverage gate keeps the suite present.
**Mode**: mvp
**Depends on**: Phase 2
**Requirements**: TEST-01, TEST-02, TEST-04, TEST-05
**Success Criteria** (what must be TRUE):

  1. All six `/hplc/api/*` endpoints have endpoint tests (happy path, 400, 404, session-guard) that pass in CI.
  2. The progress-endpoint regression test establishes a session, POSTs a score, then asserts real `LevelProgress` data returns — any regression to the serializer bug fails this test (no vacuous empty-list assertions).
  3. The coverage gate fails CI when the API suite's coverage drops below the configured threshold.
  4. The test suite runs without self-inflicted 429s — throttling is disabled via `override_settings` in tests.

**Plans**: TBD

### Phase 4: CI/CD Deploy Gates

**Goal**: The pipeline genuinely gates deploys — security scans block, deploy checks run against production settings, migrations ship before schema-touching code, and failed deploys report failure.
**Mode**: mvp
**Depends on**: Phase 3
**Requirements**: CI-01, CI-02, CI-03, CI-04, CI-05
**Success Criteria** (what must be TRUE):

  1. A push with a known vulnerable dependency fails the deploy job — `pip-audit` no longer runs with `|| true`; accepted advisories use an explicit `--ignore-vuln` allowlist.
  2. A push that violates deploy-only settings (e.g. DEBUG enabled, missing security headers) fails CI — `check --deploy` runs against production settings via the new `config/settings/ci.py`.
  3. A push adding a model change without a migration fails CI — `makemigrations --check` is enforced.
  4. `migrate` + `createcachetable` run before `sam deploy` — production schema updates ship with the release, not manually.
  5. A failed SAM update (non-`Successful` `LastUpdateStatus`) fails the deploy job — no false-success deploys.

**Plans**: TBD

### Phase 5: Score Integrity

**Goal**: HPLC scores are server-authoritative — clients cannot forge, tamper, or replay results, and the signing key has no committed fallback.
**Mode**: mvp
**Depends on**: Phase 4
**Requirements**: SCORE-01, SCORE-02, SCORE-03, SCORE-04, CI-06
**Success Criteria** (what must be TRUE):

  1. A user submitting a valid signed simulation result records the server-computed score correctly.
  2. A user tampering with any client numeric field (`score`, `min_resolution`, `total_run_time`, `max_pressure_bar`, `overpressure`) receives 400 — tampered values are ignored, never stored.
  3. A user replaying the same signed result twice receives 400 on the second submission — the unique `result_token_hash` column prevents replay.
  4. Expired or invalid signed tokens (older than `max_age`, or not verifiable) are rejected with 400.
  5. The app fails fast with no `SECRET_KEY` (committed fallback removed — `ImproperlyConfigured`) — tokens cannot be forged by repo readers.

**Plans**: TBD

### Phase 6: Rate Limiting

**Goal**: Public endpoints are rate-limited at the edge (CloudFront WAF) and in-app (DRF backstop), so abuse is bounded without breaking legitimate users.
**Mode**: mvp
**Depends on**: Phase 4
**Requirements**: THROT-01, THROT-02, THROT-03, THROT-04, THROT-05
**Success Criteria** (what must be TRUE):

  1. A client exceeding the limits on the expensive (`simulate`) or write-heavy (`scores`) endpoints receives 429 responses — verified by a burst regression test (N+1 calls with cache cleared).
  2. Legitimate users within the limits are unaffected and receive normal 200 responses.
  3. `simulate` and `scores` are throttled independently with separate scopes/limits — one endpoint's abuse does not starve the other.
  4. Requests hitting the Lambda Function URL directly (bypassing CloudFront/WAF) are rejected — the WAF-bypass path is closed via a custom header check.
  5. Throttle state is shared across Lambda containers (`DatabaseCache` on RDS, `NUM_PROXIES=1`) — in-app limits are not silently multiplied by container count.

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Close the RCE | 3/3 | Complete    | 2026-08-08 |
| 2. Fix Broken Endpoints | TBD | Not started | - |
| 3. API Test Coverage | TBD | Not started | - |
| 4. CI/CD Deploy Gates | TBD | Not started | - |
| 5. Score Integrity | TBD | Not started | - |
| 6. Rate Limiting | TBD | Not started | - |
