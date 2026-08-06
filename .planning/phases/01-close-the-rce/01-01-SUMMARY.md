---
phase: 01-close-the-rce
plan: 01
subsystem: security
tags: [chempy, rce, eval, validation, django, flake8]

# Dependency graph
requires:
  - phase: 01-close-the-rce
    provides: working-tree RCE fix (restricted globals + exactly-2-segment enforcement) in equilibria.py/forms.py
provides:
  - Shared no-builtins validation boundary (calculations/security.py) imported by both form and engine layers
  - SEC-02 mock pin test proving from_string always receives SAFE_EVAL_GLOBALS
  - Engine error handler split: fixed constants instead of exception text (CWE-209)
  - Direct is_safe_equation predicate matrix (SEC-01) in test_security.py
affects: [01-02 (form-layer rewire), gsd-verify-work Phase 1 UAT]

# Actuals (#2632) — pairs with the plan's estimate (30000 tokens) to calibrate.
actuals:
  tokens: 4201
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Shared security module as single source of truth for eval-boundary acceptance rules (SEC-05)
    - Mock-based pin test to isolate a call-site contract the side-effect tests cannot distinguish
    - Differentiated engine error handling: fixed server constants, logger.exception for detail

key-files:
  created:
    - apps/chemistry_calculators/calculations/security.py
    - tests/chemistry_calculators/test_security.py
  modified:
    - apps/chemistry_calculators/calculations/equilibria.py
    - tests/chemistry_calculators/test_webapp.py

key-decisions:
  - "Import only used names at each commit boundary: Task 1 imports SAFE_EVAL_GLOBALS + is_safe_equation; Task 2 adds the two message constants when the handler split consumes them (Rule 3 flake8 F401 fix)"
  - "Kept the exactly-two-segment + SAFE_EVAL_GLOBALS call shape verbatim from the working tree (D-03 invariant — never globals_=False)"
  - "Dropped the SharedRegexDriftTests class from this plan: the drift-guard identity test lands in Plan 01-02 Task 1 (per threat model T-01-04), after forms.py is rewired"

patterns-established:
  - "Pattern: shared security module (calculations/security.py) owns every string-acceptance rule feeding chempy's eval parser; layers import, never duplicate"
  - "Pattern: mock pin test asserts the exact SAFE_EVAL_GLOBALS object identity at the from_string call site"

requirements-completed: [SEC-01, SEC-02, SEC-05, SEC-06, TEST-03]

# Coverage metadata (#1602) — one entry per shipped deliverable.
coverage:
  - id: D1
    description: "Shared security module calculations/security.py exports SAFE_FORMULA_RE, SAFE_K_VALUE_RE, SAFE_EVAL_GLOBALS, UNSAFE_EQUATION_MESSAGE, GENERIC_SOLVER_ERROR_MESSAGE, is_safe_equation; engine imports from it (no local defs)"
    requirement: SEC-05
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SecurityPredicateTests"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_from_string_always_passes_no_builtins_globals"
        status: pass
    human_judgment: false
  - id: D2
    description: "EqSystem.from_string call site pins rxn_parse_kwargs globals_ to SAFE_EVAL_GLOBALS (SEC-02) — never globals_=False, never missing"
    requirement: SEC-02
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_from_string_always_passes_no_builtins_globals"
        status: pass
    human_judgment: false
  - id: D3
    description: "Engine failures return fixed constants — UNSAFE_EQUATION_MESSAGE on validation rejection, GENERIC_SOLVER_ERROR_MESSAGE on solver failure; no exception text leaks (CWE-209 / D-06)"
    requirement: SEC-06
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_engine_validation_rejection_returns_constant"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_engine_solver_failure_returns_generic_message"
        status: pass
    human_judgment: false
  - id: D4
    description: "is_safe_equation predicate covers the SEC-01 acceptance matrix — exactly-2-segment rule, K charset, '=' presence, empty sides, 0/3+ segments, code payloads on both chempy eval paths"
    requirement: SEC-01
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SecurityPredicateTests::test_is_safe_equation_accepts_valid"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SecurityPredicateTests::test_is_safe_equation_rejects"
        status: pass
    human_judgment: false
  - id: D5
    description: "Adversarial regression: both chempy eval paths (param path in K segment, kwargs path via 3-segment lines) blocked with no side effects at the engine layer; legitimate systems (carbonate, acetic acid pKa + Ka, water autoionization) still solve"
    requirement: TEST-03
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_malicious_k_expr_never_evaluated"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_extra_semicolon_parts_rejected"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_ka_mode_engine_solve"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_restricted_globals_still_solves_legit_systems"
        status: pass
    human_judgment: false

# Metrics
duration: 11min
completed: 2026-08-06
status: complete
---

# Phase 1 Plan 1: Close the RCE — Engine Half Summary

**Shared no-builtins validation boundary (calculations/security.py) extracted from the working-tree fix, engine rewired to consume it, from_string globals contract pinned by a mock test, engine error handler split to fixed constants, and the SEC-01 predicate matrix asserted directly**

## Performance

- **Duration:** 11 min
- **Started:** 2026-08-06T01:40:22Z
- **Completed:** 2026-08-06T01:51:52Z
- **Tasks:** 3
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments

- Extracted the working-tree RCE fix into `calculations/security.py` — a single shared module owning `SAFE_FORMULA_RE`, `SAFE_K_VALUE_RE`, `SAFE_EVAL_GLOBALS`, `is_safe_equation`, and the two fixed error-copy constants — so form and engine layers can never drift apart (SEC-05).
- Rewired `equilibria.py` to import every acceptance rule from `.security`; zero local regex/globals/predicate definitions remain (verified by `assert not any(n.startswith('_SAFE') for n in vars(equilibria))`).
- Added the SEC-02 pin test (`test_from_string_always_passes_no_builtins_globals`) — the only test that isolates the globals contract from the regex gate: it asserts the exact `SAFE_EVAL_GLOBALS` object identity at the `from_string` call site, so a dropped `rxn_parse_kwargs` can never regress silently behind the regexes.
- Split the engine catch-all handler: `ValueError` → `UNSAFE_EQUATION_MESSAGE`, everything else → `GENERIC_SOLVER_ERROR_MESSAGE` + `logger.exception`; no exception text reaches callers (CWE-209 / D-06).
- Added the Ka-mode engine solve test (acetic acid `1.75e-5` → pH 2.88), closing the RESEARCH Ka-mode coverage corner.
- Added `test_security.py` with the direct SEC-01 accept/reject predicate matrix covering both chempy eval paths (param + kwargs), non-2-segment shapes, and charset violations.
- Full suite grew 118 → 124 tests, all green; project source flake8 clean.

## Task Commits

Each task was committed atomically:

1. **Task 1: Shared security.py wired into the engine (tracer)** - `889cc9d` (feat)
2. **Task 2: Engine error contract — differentiated handler** - `6f5441b` (fix)
3. **Task 3: Direct is_safe_equation unit tests (SEC-01 matrix)** - `7dc2e79` (test)

**Plan metadata:** pending final docs commit

## Files Created/Modified

- `apps/chemistry_calculators/calculations/security.py` - NEW — shared no-builtins validation boundary (regexes, eval globals, predicate, error constants)
- `apps/chemistry_calculators/calculations/equilibria.py` - MOD — imports rules from `.security`, split error handler, keeps `rxn_parse_kwargs={"globals_": SAFE_EVAL_GLOBALS}` call shape
- `tests/chemistry_calculators/test_webapp.py` - MOD — SEC-02 pin test, engine error-copy assertions, Ka-mode engine solve test (+ imports)
- `tests/chemistry_calculators/test_security.py` - NEW — `SecurityPredicateTests` accept/reject matrix (SEC-01)

## Decisions Made

- **Per-commit import discipline (Rule 3):** Task 1 imports only `SAFE_EVAL_GLOBALS` + `is_safe_equation` (the names consumed at that boundary); the two message constants join the import in Task 2 where the handler split consumes them. The plan's Task 1 action imported all four names at once, but flake8 F401 flagged the two as-yet-unused constants — this split keeps every commit flake8-clean while reaching the same end state (all four names imported).
- **Drift-guard test deferred to Plan 01-02 (as planned):** the PATTERNS.md `SharedRegexDriftTests` skeleton was not added here — the threat model (T-01-04) explicitly lands the drift-guard identity test in Plan 01-02 Task 1 after `forms.py` is rewired to import `SAFE_FORMULA_RE`. Adding it now would fail (`KeyError`) because `forms.py` still defines its local `_SAFE_FORMULA_RE`.
- **Unused `json`/`os` scaffold imports dropped (Rule 3):** the plan's test_security.py action listed `json`/`os` per the scaffold convention, but neither is used in this file (payloads are inline, no marker-file assertions needed at the predicate layer) — leaving them would fail the flake8 acceptance gate.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Import split keeps Task 1 flake8-clean**
- **Found during:** Task 1 (shared security.py extraction)
- **Issue:** The plan's Task 1 action imported all four names from `.security`, but `GENERIC_SOLVER_ERROR_MESSAGE`/`UNSAFE_EQUATION_MESSAGE` are not consumed until Task 2's handler split — flake8 F401 failed at the Task 1 commit boundary.
- **Fix:** Task 1 imports `SAFE_EVAL_GLOBALS, is_safe_equation` only; Task 2 adds the two message constants when the split handler uses them. End state identical to plan (all four imported).
- **Files modified:** apps/chemistry_calculators/calculations/equilibria.py
- **Verification:** flake8 clean at each commit; full suite green; final import block matches plan spec
- **Committed in:** 889cc9d (Task 1), 6f5441b (Task 2)

**2. [Rule 3 - Blocking] Dropped unused json/os scaffold imports in test_security.py**
- **Found during:** Task 3 (test_security.py creation)
- **Issue:** Plan scaffold listed `import json; import os`, but neither is used at the predicate layer — flake8 F401 failed the acceptance gate.
- **Fix:** Imports limited to `django.conf.settings`, `django.test.SimpleTestCase`, and `chemistry_calculators.calculations.security` (plus `settings.SECRET_KEY = "test"` per scaffold).
- **Files modified:** tests/chemistry_calculators/test_security.py
- **Verification:** flake8 clean; pytest collects and passes
- **Committed in:** 7dc2e79 (Task 3)

**3. [Rule 3 - Blocking] SharedRegexDriftTests omitted (scoped to Plan 01-02)**
- **Found during:** Task 3 (test_security.py creation)
- **Issue:** PATTERNS.md sketches a `SharedRegexDriftTests` class, but the plan's own threat model (T-01-04) assigns the drift-guard identity test to Plan 01-02 Task 1 — it cannot pass today because `forms.py` still defines its own `_SAFE_FORMULA_RE`.
- **Fix:** Omitted the class; kept `SecurityPredicateTests` (the plan's Task 3 deliverable).
- **Files modified:** tests/chemistry_calculators/test_security.py (as written)
- **Verification:** plan acceptance criteria met (accept/reject tables + flake8 + pytest collect)
- **Committed in:** 7dc2e79 (Task 3)

---

**Total deviations:** 3 auto-fixed (3 Rule 3 blocking)
**Impact on plan:** All three were commit-boundary or cross-plan scoping corrections; no security-relevant behavior changed. The D-03 security invariant, the SEC-02 pin, and the error-copy contract landed exactly as planned.

## Issues Encountered

- **`flake8 .` full-repo crash (pre-existing, out of scope):** flake8 7.0 does not respect `.gitignore` (no `--respect-gitignore` flag), so `flake8 .` walks `.venv` and pyflakes raises `RecursionError` on sympy's auto-generated `resolvent_lookup.py`. CI runs `flake8 .` inside Docker with no `.venv`, so this never blocks CI. Verified project source (`apps tests config manage.py`) is flake8-clean; this condition pre-dates the plan (noted, not fixed — out of scope).
- **LSP import-resolution noise:** the language server cannot resolve `.venv`-installed packages (chempy, django) — all diagnostics are environment artifacts, not real errors; pytest confirms imports resolve.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- **Plan 01-02 scope fully unblocked:** the engine half of the shared boundary is in place; Plan 01-02 rewires `forms.py` (delete local `_SAFE_FORMULA_RE`, import `SAFE_FORMULA_RE` from `.security`), adds the generic JSON error copy, the view-layer kwargs-path tripwire, and the drift-guard identity test.
- **Deferred (unchanged):** a11y `role="alert"` template lines land with Plan 01-02's template task per UI-SPEC.
- The SEC-02 pin test is the load-bearing gate for the whole phase: any future diff that drops `rxn_parse_kwargs` or binds globals to anything other than `SAFE_EVAL_GLOBALS` fails immediately.

---

*Phase: 01-close-the-rce*
*Completed: 2026-08-06*

## Self-Check: PASSED

- FOUND: apps/chemistry_calculators/calculations/security.py
- FOUND: tests/chemistry_calculators/test_security.py
- FOUND: .planning/phases/01-close-the-rce/01-01-SUMMARY.md
- FOUND: 889cc9d (feat: extract shared security boundary)
- FOUND: 6f5441b (fix: split engine error handler)
- FOUND: 7dc2e79 (test: is_safe_equation predicate matrix)
