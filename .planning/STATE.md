---
gsd_state_version: '1.0'
status: planning
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-05)

**Core value:** Chemistry calculations and HPLC simulations must return correct, physically-sound results — and the public calculator/API endpoints must not be exploitable.
**Current focus:** Phase 1 — Close the RCE

## Current Position

Phase: 1 of 6 (Close the RCE)
Plan: 0 of 0 (plans not yet created)
Status: Ready to plan
Last activity: 2026-08-05 — Roadmap created; 30/30 requirements mapped across 6 phases

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: n/a
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Close the RCE | TBD | - | - |
| 2. Fix Broken Endpoints | TBD | - | - |
| 3. API Test Coverage | TBD | - | - |
| 4. CI/CD Deploy Gates | TBD | - | - |
| 5. Score Integrity | TBD | - | - |
| 6. Rate Limiting | TBD | - | - |

*Updated after each plan completion*

## Accumulated Context

### Decisions

Full log in PROJECT.md Key Decisions. Recent decisions affecting current work:

- [Phase 1]: Keep the working-tree RCE fix — restricted globals dict `{"__builtins__": {}}` + exactly-2-segment enforcement — NOT `globals_=False` (chempy kwargs-eval auto-injects builtins and nulls K). Treat any diff reverting it as a blocker.
- [Phase 2]: Fix `LevelProgressSerializer` as a unit (`model = LevelProgress` + import) but never merge without its endpoint test (lands Phase 3).
- [Phase 4]: CI/CD gates must land before Phases 5-6 — they add a column (`result_token_hash`) and a cache table the pipeline must be able to apply.
- [Phase 5]: Remove the `base.py` SECRET_KEY fallback before/with signing — a committed key makes tokens forgeable by repo readers.
- [Phase 6]: WAF rate rule is primary enforcement; DRF `ScopedRateThrottle` on `DatabaseCache` (`NUM_PROXIES=1`) is the documented backstop. LocMemCache throttling on Lambda throttles nothing.

### Pending Todos

None yet.

### Blockers/Concerns

From research SUMMARY.md "Research Flags" (revisit during plan-phase):

- [Phase 1]: Confirm chempy 0.9.0 accepts `rxn_parse_kwargs={"globals_": {...}}` — one-line REPL check.
- [Phase 4]: Decide `pip-audit --ignore-vuln` allowlist policy; re-check `--fail-on` exit-code semantics against pinned version; decide whether to fully pin numpy/scipy.
- [Phase 5]: Confirm replay-window vs unique-token-hash tradeoff; document SECRET_KEY rotation story (rotation invalidates outstanding tokens — acceptable).
- [Phase 6]: WAF rate parameters need load data; validate `NUM_PROXIES`/CloudFront XFF forwarding with a real request trace; decide direct-Function-URL fix (custom header vs API Gateway migration).

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-08-05 — Roadmap created (6 phases, 30/30 coverage)
Stopped at: All artifacts written (ROADMAP.md, STATE.md, REQUIREMENTS.md traceability)
Resume file: None
