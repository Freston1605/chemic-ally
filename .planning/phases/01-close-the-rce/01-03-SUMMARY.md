---
phase: 01-close-the-rce
plan: 03
subsystem: security
tags: [chempy, rce, eval, validation, django, pint, cwe-209, flake8]

# Dependency graph
requires:
  - phase: 01-close-the-rce
    provides: shared no-builtins validation boundary (calculations/security.py) with SAFE_FORMULA_RE/SAFE_K_VALUE_RE/SAFE_EVAL_GLOBALS/is_safe_equation + fixed error constants (Plan 01-01), form layer rewired + drift guard (Plan 01-02)
provides:
  - CR-01 containment: pint UndefinedUnitError (AttributeError subclass) caught in the concentrations guard — unknown units now a clean validation error with fixed copy, never HTTP 500
  - WR-01 closure: UnsafeEquationError(ValueError) dedicated to the validation-rejection signal; solver ValueErrors log logger.exception and return the generic message
  - WR-02 closure: raw "\n" guard as the first statement of is_safe_equation (load-bearing) + \Z anchors on both regexes (defense-in-depth)
  - WR-04 closure: safe_k_value evaluates K expressions under SAFE_EVAL_GLOBALS and requires math.isfinite — engine rejects 1e999/1/0 before chempy runs
  - WR-03 closure: form/engine behavioral equivalence — k_value stripped, non-string reactants/products rejected with fixed copy, clean() gates reconstructed equations with the engine's own predicate
affects: [gsd-verify-work Phase 1 UAT, future phases touching forms.py / calculations/security.py / calculations/equilibria.py]

# Actuals (#2632) — pairs with the plan's estimate (28000 tokens) to calibrate.
actuals:
  tokens: 5277
  tasks: 3
  commits: 7

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Raw-newline rejection as the FIRST statement of a strip-based predicate — anchors alone are inert when segments are stripped before regex matching (WR-02 load-bearing fix)
    - Dedicated exception type (UnsafeEquationError) for the validation-rejection signal so generic handlers classify, log, and return the right copy (WR-01)
    - safe_k_value reuses SAFE_EVAL_GLOBALS — no new eval context, layered finite-K defense for non-HTTP callers (WR-04)
    - Form-side gate on reconstructed equations using the engine's own predicate object — behavioral SEC-05 equivalence, pinned by a __globals__ identity drift guard

key-files:
  created: []
  modified:
    - apps/chemistry_calculators/calculations/security.py
    - apps/chemistry_calculators/calculations/equilibria.py
    - apps/chemistry_calculators/forms.py
    - tests/chemistry_calculators/test_security.py
    - tests/chemistry_calculators/test_webapp.py
    - .planning/phases/01-close-the-rce/01-PATTERNS.md

key-decisions:
  - "safe_k_value evaluates under SAFE_EVAL_GLOBALS (never the default eval context) — the D-03 one-way invariant is untouched and reused, not duplicated"
  - "The raw '\\n' guard, not the \\Z anchors, is the load-bearing WR-02 fix: the predicate strips ;-segments before regex matching, so anchors alone leave the newline tests red"
  - "clean() early-returns after add_error when a reconstructed equation fails the gate — the form is invalid, stop processing (concentrations parse skipped)"
  - "PATTERNS.md security.py snapshot + validation-pattern block updated to the post-gap shape (plan's post-execution follow-up, landed in this plan's docs commit)"

patterns-established:
  - "Pattern: strip-based predicates must reject raw newlines before split/strip; \\Z anchors are defense-in-depth for direct regex consumers"
  - "Pattern: validation-rejection signals get their own exception type so generic handlers can log and classify solver failures (observability)"
  - "Pattern: a form that reconstructs engine input gates the reconstruction with the engine's own predicate object — identity pinned by a __globals__ drift guard"

requirements-completed: [SEC-01, SEC-03, SEC-04, SEC-05]

# Coverage metadata (#1602) — one entry per shipped deliverable.
coverage:
  - id: D1
    description: "CR-01: unknown-unit concentrations produce a clean form-invalid response with the D-06 fixed copy 'Concentrations data could not be read.' — never HTTP 500, never the unit string in the message (CWE-209 negative assertion)"
    requirement: SEC-01
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_unknown_unit_concentration_rejected"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaViewTests::test_equilibria_view_unknown_unit_no_500"
        status: pass
    human_judgment: false
  - id: D2
    description: "WR-01: engine error classification is exact — only UnsafeEquationError returns 'Unsafe or malformed reaction string'; a genuine solver ValueError logs logger.exception ('Equilibria calculation failed') at ERROR and returns the generic message"
    requirement: SEC-06
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_solver_valueerror_returns_generic_message"
        status: pass
    human_judgment: false
  - id: D3
    description: "WR-02/WR-04: is_safe_equation rejects trailing/formula-side newline variants and non-finite/zero-division K expressions (raw \\n guard + \\Z anchors + safe_k_value under SAFE_EVAL_GLOBALS); the engine rejects 1e999 before chempy runs"
    requirement: SEC-01
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SecurityPredicateTests::test_is_safe_equation_rejects_newline_and_non_finite"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SecurityPredicateTests::test_safe_k_value_accepts_finite"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SecurityPredicateTests::test_safe_k_value_rejects_non_finite"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_engine_rejects_non_finite_k"
        status: pass
    human_judgment: false
  - id: D4
    description: "WR-03: form/engine behavioral equivalence — whitespace-padded K values normalize end-to-end to engine-accepted expressions, non-string reactants/products rejected with exact 'must be a string.' copy, clean() gates every reconstructed equation with the engine's own predicate (identity drift guard extended to forms.clean.__globals__)"
    requirement: SEC-05
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_k_value_whitespace_padded_accepted"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_non_string_reactants_products_rejected"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_clean_never_emits_equation_engine_rejects"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SharedRegexDriftTests::test_forms_and_engine_share_same_regex"
        status: pass
    human_judgment: false
  - id: D5
    description: "No invariant regression: SAFE_EVAL_GLOBALS byte-identical, SEC-02 pin green, no globals_=False code path, no str(e) in user-facing paths, full suite green (138), flake8 clean on project source"
    requirement: SEC-02
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaCalculatorTests::test_from_string_always_passes_no_builtins_globals"
        status: pass
      - kind: other
        ref: "pytest -q → 138 passed; flake8 apps tests config manage.py → exit 0; grep SAFE_EVAL_GLOBALS pin matches"
        status: pass
    human_judgment: false

# Metrics
duration: 8min
completed: 2026-08-08
status: complete
---

# Phase 1 Plan 3: Close the RCE — Gap Closure Summary

**All five verification gaps closed: pint UndefinedUnitError contained as a clean validation error (CR-01, never HTTP 500), UnsafeEquationError classification with exact logging (WR-01), raw-newline guard + \Z anchors making the rejection contract exact (WR-02), engine-side finite-K gate via safe_k_value under SAFE_EVAL_GLOBALS (WR-04), and form/engine behavioral equivalence with clean() gating on the engine's own predicate (WR-03)**

## Performance

- **Duration:** 8 min
- **Started:** 2026-08-08T18:05:55Z
- **Completed:** 2026-08-08T18:14:16Z
- **Tasks:** 3
- **Files modified:** 5 (0 created, 5 modified) + PATTERNS.md follow-up

## Accomplishments

- **CR-01 closed (blocking):** the concentrations guard in `EquilibriumSystemForm.clean()` now catches `AttributeError` — pint 0.24.4's `UndefinedUnitError` MROs through it — so an attacker-controlled unit string yields the D-06 fixed copy "Concentrations data could not be read." instead of an HTTP 500. Pinned at both the form level (`test_unknown_unit_concentration_rejected`, with a CWE-209 `assertNotIn("notAUnit")` negative assertion) and through the full HTTP stack (`test_equilibria_view_unknown_unit_no_500` asserting status 200 explicitly).
- **WR-01 closed:** `equilibria.py` defines `UnsafeEquationError(ValueError)`, raised only by the validation loop and caught by a dedicated branch returning `UNSAFE_EQUATION_MESSAGE`. Genuine solver/parser `ValueError`s now fall to the generic branch, which logs `logger.exception("Equilibria calculation failed")` and returns `GENERIC_SOLVER_ERROR_MESSAGE`. `test_solver_valueerror_returns_generic_message` pins classification + logging via `assertLogs` at ERROR level; `except ValueError` no longer appears in equilibria.py.
- **WR-02 closed:** the raw `if "\n" in equation: return False` guard is now the FIRST statement of `is_safe_equation` (before split/strip — the predicate strips each `;`-segment before regex matching, so `\Z` anchors alone cannot reject a trailing/formula-side newline), and both regexes are re-anchored `$` → `\Z` as defense-in-depth for direct consumers. Charsets byte-identical to the pre-gap versions.
- **WR-04 closed:** new `safe_k_value()` evaluates the K expression under `SAFE_EVAL_GLOBALS` (the same no-builtins globals chempy's internal eval uses — no new attack surface) and requires `math.isfinite`, wired into `is_safe_equation`. `calculate(["H2O = H+ + OH-; 1e999"])` now returns success False with the validation message (was success True, pH -0.043).
- **WR-03 closed:** the form can no longer emit an equation the engine rejects — `k_value` is stripped before `float()` and before reconstruction (`" 10.3 "` → `"10**-10.3"` end-to-end), non-string reactants/products fail fast with exact "must be a string." messages (killing the `str()`-coercion divergence), and `clean()` gates every reconstructed equation with the engine's own `is_safe_equation` object, adding `UNSAFE_EQUATION_MESSAGE` to `errors["reactions"]` with an early return. The SEC-05 drift guard now also asserts the predicate identity in `forms.clean.__globals__`.
- Full suite grew 128 → 138 tests (10 new, exactly as planned), all green; flake8 clean on project source; PATTERNS.md snapshot updated per the plan's post-execution follow-up.

## Task Commits

Each task was committed atomically (TDD RED → GREEN):

1. **Task 1: Unknown concentration units become clean validation errors (CR-01, tracer)** - `3649bc7` (test) + `689174a` (fix)
2. **Task 2: Engine boundary — UnsafeEquationError, true-end-of-string anchors, finite-K gate (WR-01/02/04)** - `e618a5d` (test) + `efadae9` (feat)
3. **Task 3: Form/engine behavioral equivalence (WR-03)** - `a41244b` (test) + `2170564` (feat)

**Plan metadata:** pending final docs commit

## Files Created/Modified

- `apps/chemistry_calculators/calculations/security.py` - MOD — raw `"\n"` guard as first statement of `is_safe_equation`; both regexes re-anchored `$` → `\Z` (charsets unchanged); new `safe_k_value()` (charset check → eval under SAFE_EVAL_GLOBALS → `math.isfinite`); `is_safe_equation` K gate routed through `safe_k_value`
- `apps/chemistry_calculators/calculations/equilibria.py` - MOD — new `UnsafeEquationError(ValueError)`; validation loop raises it; handler split to `except UnsafeEquationError` / generic `except Exception` (logger.exception unchanged)
- `apps/chemistry_calculators/forms.py` - MOD — guard tuple widened with `AttributeError` (pint MRO comment); import extended to `UNSAFE_EQUATION_MESSAGE, is_safe_equation`; `isinstance(str)` checks on reactants/products; `k_value` normalized with `str().strip()`; `clean()` gates every reconstructed equation with `is_safe_equation` + early return
- `tests/chemistry_calculators/test_webapp.py` - MOD — 2 CR-01 tests, 2 WR-01/WR-04 engine tests, 3 WR-03 form tests
- `tests/chemistry_calculators/test_security.py` - MOD — newline/non-finite reject-table test, `test_safe_k_value_accepts_finite` / `test_safe_k_value_rejects_non_finite`, drift guard extended to `forms.clean.__globals__["is_safe_equation"]`
- `.planning/phases/01-close-the-rce/01-PATTERNS.md` - MOD — security.py snapshot + validation pattern updated to the post-gap shape (`\Z`, raw guard, `safe_k_value`, `UnsafeEquationError`)

## Decisions Made

- **safe_k_value reuses SAFE_EVAL_GLOBALS (D-03):** the finite-K gate evaluates under the exact `{"__builtins__": {}}` globals chempy's internal eval uses — no new eval context, so the one-way no-builtins invariant is reused rather than duplicated (plan's reversibility note honored).
- **Raw-newline guard is the load-bearing WR-02 fix:** `\Z` anchors alone are inert because the predicate strips `;`-segments before regex matching (verified in RED: the newline tests stayed red against an anchors-only change). The `\n` check is safe for both call paths — the engine validates each equation individually before joining with `\n`, and the form reconstructs equations newline-free.
- **clean() early-return on gate failure:** after `add_error("reactions", UNSAFE_EQUATION_MESSAGE)`, processing stops (concentrations parse skipped) — the form is invalid, no further work is meaningful.
- **PATTERNS.md updated in-plan (follow-up executed):** the plan's post-execution follow-up (stale security.py snapshot at lines 36-67 + validation-pattern block) landed in this plan's docs commit.

## Deviations from Plan

None - plan executed exactly as written. All three TDD tasks completed RED → GREEN with the acceptance criteria verified per task; the plan's post-execution PATTERNS.md follow-up was executed (not deferred).

## Issues Encountered

- **Test patch-scoping fix during GREEN (Task 3):** `test_clean_never_emits_equation_engine_rejects` initially called `form.is_valid()` outside the `patch("chemistry_calculators.forms.is_safe_equation", ...)` context, so the patch was inert at validation time and the test failed against the fixed implementation. Moved `is_valid()` + assertions inside the `with` block (matching the plan's sketch) — test then passed. Corrected within the Task 3 GREEN commit; both commits remain atomic.
- **`flake8 .` full-repo crash (pre-existing, out of scope):** identical to the 01-01/01-02-documented condition — flake8 7.0 does not respect `.gitignore`, so walking `.venv` trips `RecursionError` on sympy's auto-generated `resolvent_lookup.py`. CI runs `flake8 .` inside Docker without `.venv`, so this never blocks CI. Verified project source (`apps tests config manage.py`) is flake8-clean (exit 0).
- **LSP import-resolution noise:** the language server cannot resolve `.venv`-installed packages (django, chempy, pint) — all diagnostics are environment artifacts, not real errors; pytest confirms all imports resolve.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- **Phase 01-close-the-rce fully complete:** all five verification gaps (CR-01 + WR-01..WR-04) closed, all 7 phase requirement IDs satisfied (SEC-01..06, TEST-03 — this plan hardened SEC-01/03/04/05; SEC-02/06/TEST-03 re-verified green by the plan gate), 138 tests green, flake8 clean.
- **End-of-phase human-check pending (UAT):** the deferred a11y check from 01-02 — load `/equilibria` locally, submit an invalid reaction and confirm the error renders inside a screen-reader-announced `role="alert"` region, then confirm a valid query (e.g. `HCO3- = H+ + CO3-2; 10**-10.3`) still renders the pH value and species table.
- **Phase 2 (Fix Broken Endpoints)** is the next phase per ROADMAP — `LevelProgressSerializer` misconfiguration (`GET /hplc/api/progress/` 500s), with its endpoint test deferred to Phase 3 per the logged Phase 2 decision.

---

*Phase: 01-close-the-rce*
*Completed: 2026-08-08*

## Self-Check: PASSED

- FOUND: apps/chemistry_calculators/calculations/security.py
- FOUND: apps/chemistry_calculators/calculations/equilibria.py
- FOUND: apps/chemistry_calculators/forms.py
- FOUND: tests/chemistry_calculators/test_security.py
- FOUND: tests/chemistry_calculators/test_webapp.py
- FOUND: .planning/phases/01-close-the-rce/01-03-SUMMARY.md
- FOUND: 3649bc7 (test(01-03): unknown-unit containment)
- FOUND: 689174a (fix(01-03): UndefinedUnitError containment)
- FOUND: e618a5d (test(01-03): engine boundary classification)
- FOUND: efadae9 (feat(01-03): engine boundary)
- FOUND: a41244b (test(01-03): behavioral equivalence)
- FOUND: 2170564 (feat(01-03): behavioral equivalence)
