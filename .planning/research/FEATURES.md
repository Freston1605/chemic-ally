# Feature Research

**Domain:** Production-hardened Django 5.2 + DRF chemistry calculator web app (serverless AWS Lambda, anonymous session identity, gamified HPLC simulator with a JSON API)
**Researched:** 2026-08-05
**Confidence:** HIGH (library behaviors verified against official docs/source); MEDIUM where noted (domain synthesis)

## Feature Landscape

Features are categorized for the **hardening milestone** (HARD-01…HARD-08 in PROJECT.md). This milestone is about making a publicly-exposed, session-only Django/DRF app safe and verifiable — not adding user-facing functionality. Every feature below maps to one of five hardening areas: (a) parser-boundary security, (b) DRF API robustness, (c) API test coverage, (d) score integrity, (e) CI/CD security gates.

### Table Stakes (Must Have to Ship the Hardening Milestone)

These are non-negotiable: the app is already live on a public Function URL (`AuthType: NONE`), so any gap here is an exploitable or broken behavior today.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| (a) Parser-boundary eval elimination — chempy must never `eval()` user input | A live RCE was found (`EqSystem.from_string` without `globals_=False`); chempy's own docs call eval "a severe security concern for untrusted input" | LOW (in progress) | Working tree already has `globals_=_SAFE_EVAL_GLOBALS` (`{"__builtins__": {}}`) + numeric-only K regex + formula-charset regex + `_is_safe_equation` line validator. Verify `globals_=False` vs restricted dict actually parses valid K expressions before merging (see Pitfalls). HARD-01 |
| (a) Server-side numeric validation of every client-supplied JSON field (K values, concentrations) | The hidden-JSON `reactions` field was fully user-controlled; `k_value` was interpolated into an eval'd string | MEDIUM | `float()` + `math.isfinite` rejection already added in `clean_reactions`; keep it. HARD-01 |
| (a) Input length caps on hidden JSON CharFields | Blocks oversized-payload abuse (OWASP API4 resource consumption) | LOW | `max_length=5000` already on `reactions`/`concentrations`; verify it's honored and add a regression test |
| (a→c) Adversarial regression tests for the RCE payload set | No regression guard exists for the most critical security fix in the codebase | MEDIUM | Test payloads: `"; __import__('os')"`, `"__builtins__"`, `"10**-1e999"` (overflow), oversized JSON, non-numeric k_value, weird formula chars. HARD-06 |
| (b) API test coverage for all six `/hplc/api/*` endpoints | Zero DRF view tests shipped the broken `LevelProgressSerializer` (every progress GET 500s for existing sessions) | MEDIUM | APITestCase + APIClient, `format='json'`, status helpers. Test the full flow: simulate → submit score → progress → history with a persistent session. HARD-05 |
| (b) Fix `LevelProgressSerializer` model mismatch (`model = UserScore` with `LevelProgress`-only fields) | `/hplc/api/progress/` 500s for any session — a broken public endpoint | LOW | `model = LevelProgress`; covered by HARD-05 once written. HARD-02 |
| (b) No internal exception detail in API responses | `SimulateView` returns `{'error': 'Simulation failed', 'detail': str(e)}` — leaks paths/library internals to anonymous callers | LOW | `logger.exception(...)` (already present) + return generic `'Simulation failed'`; use an `APIException` subclass with safe `default_detail` for consistent shape |
| (b) DRF throttling / rate limiting on public endpoints | CPU-bound `SimulateView` (numpy linspace 5000 pts + per-analyte EMG) is hammerable for free → Lambda cost abuse / DoS (OWASP API4, API6) | MEDIUM | `ScopedRateThrottle` with a `simulate` scope (e.g. `'simulate': '10/min'`) + `AnonRateThrottle` global. **Must** key on session or correctly parse `X-Forwarded-For` behind CloudFront (`NUM_PROXIES`), else all users share edge IPs → effectively a global throttle (see Pitfalls). HARD-08 |
| (b) Reaction-balancing error handling: no implicit `None`, require both sides | `BalanceChemicalReaction` returns `None` → template renders broken result; missing-reactants-only input fails cryptically | LOW | Return explicit `{"error": ...}`; raise a typed exception from `ReactionBalancer.calculate`; require reactants AND products in `clean`. HARD-03/04 |
| (d) Server-authoritative scoring — never trust client `score` | Anyone can `POST score: 999999999` and top every level; `overpressure` flag bypasses the only cross-field check | MEDIUM | Recompute score server-side from `min_resolution`/`total_run_time`/`max_pressure_bar` via the engine's `calculate_score` (exists), or accept a server-signed result payload (next row). HARD-07 |
| (e) `check --deploy` runs against **production** settings in CI | Current CI sets `DJANGO_SETTINGS_MODULE=config.settings.development` → all deploy-only security checks (W009/W018/W020, headers, secure cookies) are silently skipped | MEDIUM | Inject required env vars (SECRET_KEY, ALLOWED_HOSTS, RDS vars) into the test job and run `check --deploy` with `config.settings.production` (it hard-fails without them — that's the point) |
| (e) `pip-audit` as a **blocking** gate | `pip-audit ... || true` swallows findings; deploy proceeds with known-vulnerable deps | LOW | Remove `|| true` (exit 1 on any vuln). Optionally filter high/critical via JSON output + jq first. Use `--ignore-vuln ID` for acknowledged noise. Official action: `pypa/gh-action-pip-audit@v1.1.0` |

### Differentiators (Competitive Advantage)

Not required, but they raise the bar beyond a typical hobby chemistry tool and directly serve the project's Core Value ("must not be exploitable; results must be correct").

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| (d) Signed result tokens for score submission | Genuinely unusual rigor for an anonymous gamified tool: server issues a `TimestampSigner`-signed payload (sim params + computed score) at simulate time; submission verifies signature + `max_age`. Makes trivial forgery impossible without the SECRET_KEY | MEDIUM | Uses Django's `django.core.signing` (`dumps`/`loads` with `TimestampSigner` + `max_age`). Requires removing the hardcoded SECRET_KEY fallback first (it's in the repo) |
| (c) Deterministic seeded simulation engine | Reproducible chromatograms: identical inputs → identical outputs (currently unseeded `np.random.normal`). Enables golden-value testing, reproducible bug reports, "share this result" UX | LOW | Add a `seed` param → `np.random.default_rng(seed)`; derive from request or fixed default. Prerequisite for golden tests |
| (c) Golden-value regression tests bound to `SCIENTIFIC_LOGIC.md` invariants | Locks the physics: LSS retention, plate count, pressure, EMG shape against recorded outputs with tolerance (`np.allclose`/`pytest.approx`) | MEDIUM | Requires RNG seeding first; assert physical invariants, not just outputs |
| (a) Documented defense-in-depth on the chempy parser | The fix is layered (charset regexes + no-builtins eval + line validator + numeric K validation); a written threat model for why each layer exists survives future maintainers | LOW | One paragraph in `SCIENTIFIC_LOGIC.md`-style doc or the module docstring; ties to the untrusted-input boundary |
| (e) Coverage gate on the API layer | `coverage report --fail-under` (exit 2) makes the API test suite a permanent regression surface rather than a one-time fix | LOW | Only after HARD-05/06 land; set a realistic floor for `apps/hplc_simulator/` + `apps/chemistry_calculators/` separately, not one global number |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| User accounts / auth migration to fix score integrity | "Scores are anonymous and forgeable — make people log in" | Blocks the whole roadmap (PROJECT.md out-of-scope); adds auth surface (OWASP API2/API5 classes), password storage, recovery flows to a tool that has zero accounts today | Server-authoritative scoring + signed tokens + throttling (HARD-07/08). Accounts only return for a future leaderboard |
| Roll-your-own eval sandbox (AST whitelist, restricted Python) | "We need `10**-x` evaluated but no code execution" | Every custom sandbox is a research project with a long exploit history; chempy already ships the documented opt-out | `globals_=False` (no eval at all) or `{"__builtins__": {}}` + numeric-only K regex (in working tree). If `globals_=False` breaks K parsing, keep the regex-gated dict |
| Rebuild chempy `EqSystem` from parsed dicts instead of strings | "Then no string parsing at all" | Rewrites the equilibria solver's input contract; high risk to a working calculation engine mid-hardening; CONCERNS.md lists it only as a migration path | Validation layers already neutralize the vector; revisit only if chempy's parser changes |
| Client-side (browser) score signing or HMAC with a shared secret | "Verify scores without server recompute" | The secret must live in the browser → trivially extractable; pure theater | Server-issued signed tokens (secret stays server-side) |
| CloudFront WAF as the *only* rate limiting | "Throttle at the edge, no app changes" | WAF rate rules are per-IP and cost per request; they don't stop distributed abuse of CPU-bound endpoints and don't protect the DB-write path; DRF throttling is free | DRF `ScopedRateThrottle` first; WAF later as an edge complement, not a replacement |
| Per-IP throttling as the only identity | "Simplest throttle" | `X-Forwarded-For` is client-controllable upstream of your app unless NUM_PROXIES is exactly right; behind CloudFront NAT'd/IPv6 pools collapse many users into one bucket | Key throttles on session_key when present (all simulator traffic has one), IP only as fallback |
| CAPTCHA / proof-of-work on the simulator | "Stop bots" | Kills UX on a tool for exploring; doesn't solve CPU-cost abuse better than scoped throttling | Scoped throttles per endpoint with generous-but-bounded rates |
| Honeypots / obfuscated client scoring | "Confuse cheaters" | Security-by-obscurity; adds code to maintain, blocks no one | Signed server-issued tokens (the real fix) |
| Add Redis/Celery for rate-limit counters and async simulation | "Scalable throttling and job queue" | Violates the no-Redis/no-Celery constraint; DRF's cache-based throttles can use the existing Django cache (locmem is fine at this scale); simulation is 30s-timeout-compatible | Stay on the existing stack; only revisit at real traffic |
| Global leaderboard / cross-session rankings | "Gamification!" | Requires real identity (accounts) — explicitly deferred; anonymous sessions can't dedupe cheaters or people | Keep per-session progress; revisit after accounts |

## Feature Dependencies

```
[A] Parser-boundary hardening (HARD-01) ──requires──> [B] Adversarial regression tests (HARD-06)
[C] API test coverage (HARD-05) ──requires──> [D] Progress-serializer fix (HARD-02)
[C] ──requires──> [E] Score-integrity fix (HARD-07)   (tests must assert server authority)
[F] Signed score tokens ──requires──> [G] SECRET_KEY fallback removal (base.py raises ImproperlyConfigured)
[F] ──requires──> [H] Server-side calculate_score (already exists in engine.py)
[I] Golden-value tests ──requires──> [J] RNG seeding in generate_chromatogram
[K] Coverage gate (--fail-under) ──requires──> [C] + [B]   (gate before tests = red CI)
[L] check --deploy w/ production settings ──requires──> [M] env-var injection in CI job (secrets)
[N] pip-audit blocking ──enhances──> [O] dependency locking   (fewer noisy findings on pinned set)
[P] DRF throttling ──enhances──> [Q] NUM_PROXIES/custom session-keyed throttle (CloudFront correctness)
[R] Score submission throttle ──enhances──> [S] Signed score tokens   (belt-and-suspenders on API6 abuse)
```

### Dependency Notes

- **[A] → [B]:** The RCE fix is meaningless without a regression guard; both should land in the same phase. HARD-06 exists precisely for this.
- **[C] → [D]:** You cannot write meaningful tests for `/hplc/api/progress/` while it 500s. Fix the serializer first, then the tests prove it.
- **[F] → [G]:** Signed tokens are only secure if the signing key is a production secret. The hardcoded `base.py` fallback `SECRET_KEY` must be removed first (also required to silence W009 under production `check --deploy`).
- **[I] → [J]:** Unseeded `np.random.normal` makes golden values impossible; seeding is the prerequisite, not a nice-to-have.
- **[L] → [M]:** `config.settings.production` raises `ImproperlyConfigured` without RDS/SECRET_KEY/ALLOWED_HOSTS env vars, so the CI job must inject them (from GitHub secrets) into the `check --deploy` step only.
- **[N] → [O]:** A blocking pip-audit on unpinned ranges (`numpy>=2.1,<3`, `scipy>=1.14,<2`) produces drift-prone, hard-to-triage results; pin fully (or add a lockfile) in the same milestone.
- **[P] → [Q]:** Default `AnonRateThrottle` behind CloudFront keys on edge IPs unless `NUM_PROXIES` is configured or a custom throttle prefers `session_key` — get this right or the throttle is effectively global (and spoofable).
- **[R] → [S]:** Throttling submissions reduces, but doesn't prevent, forgery; signing closes the loop. Do both in HARD-07/08.

## MVP Definition

### Launch With (v1 — the hardening milestone)

Ruthless minimum: close the RCE, make every endpoint correct and tested, stop trivial forgery, and make the CI gates actually block.

- [ ] **Parser-boundary hardening** (HARD-01) — the RCE is live today; nothing else ships before this
- [ ] **Adversarial regression tests** (HARD-06) — the guard for HARD-01; same phase
- [ ] **API test coverage for all six endpoints** (HARD-05) — proves HARD-02/03/04/07 and prevents regression of the serializer bug class
- [ ] **`LevelProgressSerializer` fix** (HARD-02) — broken public endpoint; prerequisite to HARD-05
- [ ] **Reaction balancing fixes** (HARD-03/04) — explicit error results, both-sides validation
- [ ] **Server-authoritative scoring** (HARD-07) — recompute via `calculate_score` and/or signed result tokens
- [ ] **DRF throttling** (HARD-08) — session-keyed scoped throttles on simulate + scores
- [ ] **`check --deploy` against production settings in CI** — makes every other Django security setting (secure cookies, headers, secret key) enforceable
- [ ] **Blocking `pip-audit`** — the security scan must gate the deploy, or it's not a gate

### Add After Validation (v1.x)

- [ ] **RNG seeding + golden-value tests** — once HARD-05/06 pattern is established; the engine is the product's physics core
- [ ] **Signed score tokens** (full TimestampSigner flow) — if server-recompute alone feels insufficient; requires SECRET_KEY fallback removal
- [ ] **Coverage gate** (`--fail-under`) — after the test suites land, to keep them honest
- [ ] **SECRET_KEY fallback removal + CSRF/SESSION_COOKIE_SECURE/security headers** — cleanup surfaced by production `check --deploy`; do as soon as the CI env-var injection exists
- [ ] **Dependency locking** — pin transitive set / add lockfile to stabilize CI and pip-audit triage
- [ ] **No-exception-detail responses** across all API error paths (standardize via custom EXCEPTION_HANDLER)

### Future Consideration (v2+)

- [ ] **Error monitoring (Sentry or CloudWatch 5XX alarms)** — the 500s today surface only in CloudWatch logs with no alerting; worth doing after the milestone proves stable
- [ ] **Session-history cap** (last 20 substances) + `clearsessions` job — unbounded session rows in RDS
- [ ] **WAF rate rules** at CloudFront — edge complement once DRF throttling is in
- [ ] **RDS Proxy / PgBouncer** — only when Lambda concurrency × per-request connections threaten RDS `max_connections`
- [ ] **User accounts / leaderboards** — the only honest fix for cross-session cheating; explicitly deferred in PROJECT.md

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Parser-boundary hardening (HARD-01) | HIGH (RCE live) | LOW (in progress) | P1 |
| Adversarial regression tests (HARD-06) | HIGH | MEDIUM | P1 |
| API test coverage for 6 endpoints (HARD-05) | HIGH (proves correctness) | MEDIUM | P1 |
| LevelProgressSerializer fix (HARD-02) | HIGH (broken endpoint) | LOW | P1 |
| Reaction-balancing error handling (HARD-03/04) | MEDIUM | LOW | P1 |
| Server-authoritative scoring (HARD-07) | HIGH (forgery live) | MEDIUM | P1 |
| DRF throttling (HARD-08) | HIGH (cost/DoS abuse) | MEDIUM | P1 |
| check --deploy vs production in CI | HIGH (security checks off) | MEDIUM | P1 |
| Blocking pip-audit | HIGH (gate does nothing) | LOW | P1 |
| No exception-detail leaks | MEDIUM | LOW | P2 |
| RNG seeding + golden-value tests | MEDIUM | LOW→MEDIUM | P2 |
| Signed score tokens (full flow) | MEDIUM | MEDIUM | P2 |
| SECRET_KEY fallback removal | HIGH (signing integrity) | LOW | P2 (blocker for signing) |
| Coverage gate | LOW→MEDIUM | LOW | P2 |
| Dependency locking | MEDIUM | LOW | P2 |
| Error monitoring (Sentry/CloudWatch) | MEDIUM | MEDIUM | P3 |
| Session-history cap + cleanup | LOW→MEDIUM | LOW | P3 |
| WAF rate rules | LOW | MEDIUM | P3 |
| RDS Proxy | LOW (scale) | HIGH | P3 |
| User accounts / leaderboards | MEDIUM | HIGH | P3 (blocked by design) |

**Priority key:** P1 = must land in the hardening milestone; P2 = same milestone if capacity, else immediately after; P3 = deferred, needs new milestone.

## Competitor Feature Analysis

No dedicated competitor research was possible in this run (external search providers disabled; LOW confidence on market specifics). Frame below is the documented baseline for production Django/DRF APIs vs what this app does today.

| Feature | Typical chemistry tool (WebQC/ChemCalc/PubChem calculators) | Typical DRF API baseline | Our Approach (ChemicAlly) |
|---------|--------------|--------------|--------------|
| Server-side recompute of calculated values | Yes (results are authoritative server output) | Expected | Partial — calculators yes; HPLC scores no (HARD-07 closes this) |
| Rate limiting on public endpoints | Rarely (server-rendered, low abuse value) | Expected for CPU/DB-heavy endpoints | None today (HARD-08) |
| API test coverage | N/A (no public API) | Expected before shipping | Zero DRF view tests today (HARD-05) |
| Error responses without internals | N/A | Expected (`{"detail": ...}` shape) | `str(e)` leak in SimulateView (P2 fix) |
| Deploy-blocking security gates | N/A | Expected (CI must fail on vulns/misconfig) | pip-audit non-blocking + check --deploy runs on dev settings (P1 fixes) |
| Anti-cheat on gamified scores | N/A | Not standard | Would be ahead of the curve: signed server tokens + recompute |

## Sources

- **Django 5.2 `check --deploy` + deployment checklist** — HIGH. https://docs.djangoproject.com/en/5.2/ref/django-admin/#django-admin-check (via Context7, Django source `core/checks/security/base.py`) and https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/
- **DRF throttling + client identification (`NUM_PROXIES`, `X-Forwarded-For`)** — HIGH. https://www.django-rest-framework.org/api-guide/throttling/ and https://www.django-rest-framework.org/api-guide/settings/ (via Context7)
- **DRF exception handling / `EXCEPTION_HANDLER` / `APIException`** — HIGH. https://www.django-rest-framework.org/api-guide/exceptions/ (via Context7)
- **DRF testing (`APITestCase`, `APIClient`)** — HIGH. https://www.django-rest-framework.org/api-guide/testing/ (via Context7)
- **Django signing (`Signer`, `TimestampSigner`, `dumps/loads`, `BadSignature`)** — HIGH. https://docs.djangoproject.com/en/5.2/topics/signing/ (via Context7, Django source `docs/topics/signing.txt`)
- **chempy `Reaction.from_string` `globals_` semantics** — HIGH (primary source). https://github.com/bjodah/chempy/blob/master/chempy/chemistry.py ("If False: no eval will be called (useful for web-apps)"; Notes: "calls eval which is a severe security concern for untrusted input")
- **OWASP API Security Top 10 – 2023** — HIGH. https://owasp.org/API-Security/editions/2023/en/0x11-t10/
- **pip-audit exit codes / GitHub Action / security model** — HIGH. https://github.com/pypa/pip-audit (README, `pypa/gh-action-pip-audit@v1.1.0`)
- **coverage.py `--fail-under` (exit 2)** — HIGH. https://coverage.readthedocs.io/en/latest/commands/cmd_report.html
- **GitHub Actions security hardening (GITHUB_TOKEN least privilege, SHA pinning, OIDC — incl. "custom claims for OIDC unavailable in AWS", Dependabot/dependency-review, CodeQL, Scorecards)** — HIGH. https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions
- **Score-forgery/anti-cheat patterns (server-authoritative state, signed tokens, submission throttling)** — MEDIUM (domain synthesis over the verified signing mechanism; no single authoritative source)
- **Project ground truth** — HIGH (local): `CONCERNS.md` audit (RCE, serializer bug, score forging, no throttling, non-blocking scans), `PROJECT.md` HARD backlog, working-tree fix state in `apps/chemistry_calculators/calculations/equilibria.py`, `.github/workflows/deploy.yml` (dev-settings `check --deploy`, `pip-audit || true`)

---
*Feature research for: ChemicAlly hardening milestone*
*Researched: 2026-08-05*
