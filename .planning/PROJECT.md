# ChemicAlly

## What This Is

ChemicAlly is a Django web app of chemistry calculators (molecular weight, reaction balancing, dilution, equilibria) plus an HPLC chromatography simulator with a JSON API. It is deployed serverless on AWS Lambda (container image) behind CloudFront at chemic-ally.xyz. Identity is session-based (no user accounts), and the HPLC simulator persists scores/progress keyed to a `session_key`.

## Core Value

Chemistry calculations and HPLC simulations must return correct, physically-sound results — and the public calculator/API endpoints must not be exploitable.

## Requirements

### Validated

- ✓ Molecular weight calculator — chempy formula parsing + Pint units — existing
- ✓ Reaction balancing — chempy `balance_stoichiometry` — existing
- ✓ Dilution calculator — Pint unit-safe arithmetic — existing
- ✓ Equilibria calculator — chempy `EqSystem` solving — existing
- ✓ HPLC simulation engine — LSS retention, gradient elution, van Deemter plate count, EMG peaks, Kozeny-Carman pressure — existing
- ✓ HPLC levels/scoring/progress — session-keyed `UserScore`/`LevelProgress`, DRF JSON API — existing
- ✓ Serverless deployment — Lambda + Mangum + CloudFront + RDS + S3 — existing
- ✓ Seed command (`seed_hplc_data`) for Analyte/Level content — existing
- ✓ HARD-01: Chempy `eval()` RCE closed — shared no-builtins validation boundary, validated numeric K, length caps, dual-path adversarial tests — validated in Phase 1 (Close the RCE)
- ✓ HARD-06: Regression tests for adversarial/malicious equilibria inputs (both eval paths, engine + view layers, no-side-effect assertions) — validated in Phase 1

### Active

- [ ] HARD-02: Fix `LevelProgressSerializer` misconfiguration so `GET /hplc/api/progress/` returns 200 for existing sessions
- [ ] HARD-03: Fix `BalanceChemicalReaction` implicit `None` result and `ReactionBalancer` silent failures
- [ ] HARD-04: Require both reactants and products in the reaction-balancing form
- [ ] HARD-05: Add API test coverage for all six `/hplc/api/*` endpoints
- [ ] HARD-06: Add regression tests for adversarial/malicious inputs to the equilibria form
- [ ] HARD-07: Recompute or sign simulation scores server-side (anti-forgery)
- [ ] HARD-08: Add DRF throttling / rate limiting on public API endpoints

### Out of Scope

- User accounts / authentication — session identity is deliberate; migrating `UserScore`/`LevelProgress` to real accounts is a separate, larger effort — blocks global leaderboards
- HILIC/NP chromatography modes — rejected by design in serializers and model validation; reversed-phase only
- Mobile app — web-first
- New calculator/feature development — deferred until hardening milestone lands

## Context

- Django 5.2 monolith with two apps: `chemistry_calculators` (server-rendered, session-only state) and `hplc_simulator` (DRF API + the only real DB models: `Analyte`, `Level`, `UserScore`, `LevelProgress`).
- Domain engines are pure Python decoupled from HTTP: `apps/chemistry_calculators/calculations/` (chempy, Pint) and `apps/hplc_simulator/simulation/` (numpy, scipy, LSS + EMG).
- Deployed on Lambda via Mangum ASGI; production settings hard-fail on missing env vars; RDS behind VPC; S3 for static/media.
- `apps/hplc_simulator/SCIENTIFIC_LOGIC.md` is authoritative for simulation invariants — engine changes must not violate it.
- A codebase audit (`.planning/codebase/CONCERNS.md`) found a **critical RCE** via chempy `eval()` in the equilibria calculator, a broken `/hplc/api/progress/` endpoint (500s), several smaller bugs, zero API test coverage, and no rate limiting. The RCE is now closed (Phase 1: shared `calculations/security.py` boundary, restricted eval globals, error-copy hygiene, adversarial regression suite — 138 tests green).
- CI/CD (`.github/workflows/deploy.yml`) runs lint → check --deploy → pytest → security scan → Docker build → SAM deploy; there is no `manage.py migrate` step and security-scan failures are non-blocking.
- Recent work has focused on repairing the SAM/Lambda deployment pipeline (ECR, SSM, CloudFront, security-group fixes).
- Development uses SQLite + dev settings; `python manage.py seed_hplc_data` is required after migrate.

## Constraints

- **Tech stack**: Django 5.2 + DRF on AWS Lambda (Python 3.14 container), PostgreSQL/RDS, S3 — no Celery, no Redis
- **Security**: Public Function URL (`AuthType: NONE`) — any fix touching chempy string parsing must assume untrusted input
- **Compatibility**: numpy>=2.1 (engine uses `np.trapezoid`), scipy>=1.14 — keep pinned ranges honest
- **Performance**: Lambda 1024 MB / 30s timeout; `CONN_MAX_AGE=0` — no long-running or connection-reuse assumptions
- **Lint**: flake8, max line length 88, E203/W503 ignored (Black-compatible)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Harden codebase before new features | Audit surfaced a live RCE and broken endpoint; ship those fixes first | ✓ Good (Phase 1 RCE closure complete) |
| Session-based identity (no accounts) | Simplest correct model for anonymous scores; revisit for leaderboards | ✓ Good |
| Reversed-phase only in simulator | HILIC/NP rejected by design to bound scope | ✓ Good |
| Serverless Lambda + server-rendered Django | Cheap, no-ops deployment already in production | ✓ Good |

---

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-08 after Phase 1 completion*
