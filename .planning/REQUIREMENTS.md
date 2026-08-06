# Requirements: ChemicAlly — Security Hardening

**Defined:** 2026-08-05
**Core Value:** Chemistry calculations and HPLC simulations must return correct, physically-sound results — and the public calculator/API endpoints must not be exploitable.

## v1 Requirements

Requirements for the hardening milestone. Each maps to roadmap phases.

### Security — Parser Boundary (RCE)

- [x] **SEC-01**: Equilibria calculator rejects any reaction string that is not exactly a `formula = formula; K` line
- [x] **SEC-02**: chempy parsing runs with eval locked to no-builtins globals (`{"__builtins__": {}}`), never `globals_=False` or unset
- [x] **SEC-03**: K values are validated as finite numbers server-side before string interpolation
- [x] **SEC-04**: Hidden `reactions`/`concentrations` form fields have length caps
- [x] **SEC-05**: Shared validation module (`calculations/security.py`) holds the formula/K regexes used by both form and engine layers (no drift)
- [x] **SEC-06**: Regression tests assert no side effects (no file/marker written) for payloads targeting BOTH chempy eval paths (param and kwargs)

### API Robustness

- [ ] **API-01**: `GET /hplc/api/progress/` returns 200 with LevelProgress data for returning sessions (fix `LevelProgressSerializer` Meta.model + import)
- [ ] **API-02**: Reaction balancing returns explicit error results instead of implicit `None` / cryptic failure
- [ ] **API-03**: Reaction-balancing form requires both reactants AND products (non-empty)
- [ ] **API-04**: API responses never leak internal exception details (`str(e)` removed from `SimulateView` error responses)

### API Test Coverage

- [ ] **TEST-01**: All six `/hplc/api/*` endpoints have endpoint tests via `APIClient` + `reverse()` (happy / 400 / 404 / session-guard)
- [ ] **TEST-02**: The progress-endpoint regression test establishes a session and POSTs a score first (no vacuous empty-list assertions)
- [x] **TEST-03**: RCE payload tripwires are asserted at both engine and view layers
- [ ] **TEST-04**: A per-app coverage gate (`--cov-fail-under`) enforces the API suite stays present
- [ ] **TEST-05**: API tests disable throttling via `override_settings` so the suite is not self-429ing

### CI/CD Deploy Gates

- [ ] **CI-01**: `pip-audit` blocks the deploy (drop `|| true`); `safety` dropped (paywalled); `--ignore-vuln` allowlist workflow for accepted advisories
- [ ] **CI-02**: `check --deploy` runs against production settings via a new `config/settings/ci.py` (currently dev settings skip all deploy-only checks)
- [ ] **CI-03**: `makemigrations --check` fails CI when migrations are missing
- [ ] **CI-04**: Pre-deploy `migrate` + `createcachetable` run before `sam deploy` (nothing migrates production today)
- [ ] **CI-05**: Deploy verification actually fails on non-`Successful` `LastUpdateStatus` (drop `|| echo` false success)
- [ ] **CI-06**: Hardcoded `SECRET_KEY` fallback removed from `base.py` (raise `ImproperlyConfigured` like production)

### Score Integrity

- [ ] **SCORE-01**: `SimulateView` issues signed result tokens (Django `TimestampSigner`, salt `hplc-result`, `max_age` ~30 min)
- [ ] **SCORE-02**: `ScoreSubmissionView` verifies the token and recomputes from signed metrics only — ignores all client numerics (`score`, `min_resolution`, `total_run_time`, `max_pressure_bar`, `overpressure`)
- [ ] **SCORE-03**: `UserScore.result_token_hash` unique column prevents replay (tampered/replayed tokens → 400)
- [ ] **SCORE-04**: Scoring math lives in ONE module (consolidate `scoring.py` duplicate with engine `calculate_score`)

### Rate Limiting

- [ ] **THROT-01**: CloudFront WAF rate-based rule enforces limits at the edge (~2000/300s on `/hplc/api/simulate/`; scope covers `/hplc/api/*` + `/equilibria`)
- [ ] **THROT-02**: DRF `ScopedRateThrottle` backstop on shared `DatabaseCache` (on existing RDS) with `NUM_PROXIES=1`
- [ ] **THROT-03**: Scoped throttles cover the expensive `simulate` and write-heavy `scores` endpoints separately
- [ ] **THROT-04**: Direct-Function-URL WAF bypass closed (custom header check for CloudFront-originated traffic)
- [ ] **THROT-05**: Throttle regression test asserts real 429 on N+1 calls with cache cleared

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Determinism & Physics Locking

- **DET-01**: Deterministic seeded simulation engine (`np.random.default_rng(seed)`)
- **DET-02**: Golden-value tests bound to `SCIENTIFIC_LOGIC.md` invariants

### Observability

- **OBS-01**: Error monitoring (Sentry / CloudWatch 5XX alarms)
- **OBS-02**: Secret management moved to SSM Parameter Store (`RDSPassword`, `DjangoSecretKey`)

### Scale

- **SCAL-01**: RDS Proxy / PgBouncer connection pooling
- **SCAL-02**: Session history caps + `clearsessions` management command
- **SCAL-03**: Redis-backed throttling if DB-write amplification becomes measurable

## Out of Scope

| Feature | Reason |
|---------|--------|
| User accounts / authentication | Session identity is deliberate; migrating `UserScore`/`LevelProgress` to real accounts is a separate effort |
| Leaderboards / global ranking | Blocked on accounts; signing + recompute is the minimal anti-forgery fix |
| HILIC/NP chromatography modes | Rejected by design in serializers and model validation |
| Custom eval sandbox for chempy | Restricted-globals + validation gates are verified sufficient |
| WAF-only rate limiting (no app-layer throttle) | DRF throttle is the documented policy backstop; WAF is enforcement |
| Redis/Celery infrastructure | Not justified for this milestone |
| New chemistry calculators / features | Feature work deferred until hardening milestone lands |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SEC-01 | Phase 1 | Complete |
| SEC-02 | Phase 1 | Complete |
| SEC-03 | Phase 1 | Complete |
| SEC-04 | Phase 1 | Complete |
| SEC-05 | Phase 1 | Complete |
| SEC-06 | Phase 1 | Complete |
| API-01 | Phase 2 | Pending |
| API-02 | Phase 2 | Pending |
| API-03 | Phase 2 | Pending |
| API-04 | Phase 2 | Pending |
| TEST-01 | Phase 3 | Pending |
| TEST-02 | Phase 3 | Pending |
| TEST-03 | Phase 1 | Complete |
| TEST-04 | Phase 3 | Pending |
| TEST-05 | Phase 3 | Pending |
| CI-01 | Phase 4 | Pending |
| CI-02 | Phase 4 | Pending |
| CI-03 | Phase 4 | Pending |
| CI-04 | Phase 4 | Pending |
| CI-05 | Phase 4 | Pending |
| CI-06 | Phase 5 | Pending |
| SCORE-01 | Phase 5 | Pending |
| SCORE-02 | Phase 5 | Pending |
| SCORE-03 | Phase 5 | Pending |
| SCORE-04 | Phase 5 | Pending |
| THROT-01 | Phase 6 | Pending |
| THROT-02 | Phase 6 | Pending |
| THROT-03 | Phase 6 | Pending |
| THROT-04 | Phase 6 | Pending |
| THROT-05 | Phase 6 | Pending |

**Coverage:**

- v1 requirements: 30 total
- Mapped to phases: 30
- Unmapped: 0 ✓

---
*Requirements defined: 2026-08-05*
*Last updated: 2026-08-05 after roadmap creation (API-04 remapped to Phase 2; all mappings finalized)*
