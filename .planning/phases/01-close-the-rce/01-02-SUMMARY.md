---
phase: 01-close-the-rce
plan: 02
subsystem: security
tags: [chempy, rce, eval, validation, django, a11y, cwe-209]

# Dependency graph
requires:
  - phase: 01-close-the-rce
    provides: shared no-builtins validation boundary (calculations/security.py) with SAFE_FORMULA_RE, is_safe_equation, error constants (Plan 01-01)
provides:
  - Form layer rewired to import SAFE_FORMULA_RE from calculations/security.py — zero local regex definitions (SEC-05 drift elimination)
  - Fixed generic form error copy replacing JSONDecodeError exception text (D-06 / CWE-209)
  - SharedRegexDriftTests identity guard (assertIs on compiled regex + predicate)
  - View-layer kwargs-path tripwire with no-side-effect marker assertion (TEST-03 view gap closed)
  - role=alert a11y hardening on both error containers in equilibria.html (UI-SPEC a11y contract)
affects: [gsd-verify-work Phase 1 UAT (human-check: screen-reader announcement + valid-query render), future phases touching forms.py/template]

# Actuals (#2632) — pairs with the plan's estimate (22000 tokens) to calibrate.
actuals:
  tokens: 2559
  tasks: 2
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Single shared security module consumed by both form and engine layers — the drift-guard identity test (assertIs, not equality) pins the sharing contract
    - CWE-209 error-copy tests assert both the presence of fixed copy AND the absence of parser text (assertNotIn) — negative assertion guards leak variants
    - View-layer RCE tripwires assert no-side-effect (no marker file), not just form-invalid

key-files:
  created: []
  modified:
    - apps/chemistry_calculators/forms.py
    - tests/chemistry_calculators/test_security.py
    - tests/chemistry_calculators/test_webapp.py
    - templates/chemistry_calculators/calculator/equilibria.html

key-decisions:
  - "Committed the pre-existing working-tree forms.py fix (K finite-float check, 5000-char caps, charset checks) inside the Task 1 atomic commit — it is the load-bearing SEC-03/SEC-04 validation this plan keeps untouched and was never separately committed"
  - "Added assertNotIn('Expecting', ...) negative assertions to the form error-copy tests — asserts the D-06 contract that parser parse-position text never leaks, guarding against a different leak shape than the plan's assertIn"

patterns-established:
  - "Pattern: shared security module (calculations/security.py) owns every string-acceptance rule feeding chempy's eval parser; form and engine import, never duplicate — enforced by an identity drift-guard test"
  - "Pattern: user-facing error tests assert fixed-copy presence AND parser-text absence (positive + negative assertion on the same error string)"

requirements-completed: [SEC-03, SEC-04, SEC-05, SEC-06, TEST-03]

# Coverage metadata (#1602) — one entry per shipped deliverable.
coverage:
  - id: D1
    description: "Form layer imports SAFE_FORMULA_RE from calculations/security.py (zero local regex definitions); drift-guard identity test asserts clean_reactions globals and the engine predicate reference the exact security.py objects (SEC-05 / T-01-04)"
    requirement: SEC-05
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_security.py#SharedRegexDriftTests::test_forms_and_engine_share_same_regex"
        status: pass
    human_judgment: false
  - id: D2
    description: "Form JSON error copy is fixed constants — 'Reactions data could not be read.' / 'Concentrations data could not be read.' — parser exception text never reaches callers (D-06 / CWE-209 / T-01-06)"
    requirement: SEC-06
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_malformed_reactions_json_generic_copy"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_malformed_concentrations_json_generic_copy"
        status: pass
    human_judgment: false
  - id: D3
    description: "View-layer kwargs-path tripwire: POST with a 3-segment kwargs-eval payload hidden in reactants is rejected by the form charset gate and no marker file is created (TEST-03 view gap / T-01-05)"
    requirement: TEST-03
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriaViewTests::test_equilibria_view_rejects_kwargs_path_payload"
        status: pass
    human_judgment: false
  - id: D4
    description: "a11y error announcements — role=alert on the reactions-errors container and the result-card failure alert; exactly two role=alert attributes; frozen success block byte-identical (UI-SPEC a11y / T-01-07)"
    requirement: SEC-06
    verification:
      - kind: other
        ref: "grep -c 'role=\"alert\"' templates/chemistry_calculators/calculator/equilibria.html == 2"
        status: pass
    human_judgment: true
    rationale: "Screen-reader announcement behavior and the valid-query rendering of the unchanged success block require human/browser verification (end-of-phase human-check in Task 2 verify)"
  - id: D5
    description: "K finite-float validation (SEC-03) and 5000-char caps (SEC-04) remain enforced and unregressed — existing working-tree tests stay green through the rewire"
    requirement: SEC-03
    verification:
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_k_value_non_numeric_rejected"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_k_value_non_finite_rejected"
        status: pass
      - kind: unit
        ref: "tests/chemistry_calculators/test_webapp.py#EquilibriumFormTests::test_oversized_reactions_rejected"
        status: pass
    human_judgment: false

# Metrics
duration: 5min
completed: 2026-08-06
status: complete
---

# Phase 1 Plan 2: Close the RCE — HTTP Half Summary

**Form layer rewired to the shared security boundary (single SAFE_FORMULA_RE import, zero local regexes), parser-exception text replaced with fixed generic copy on both JSON branches, the view-layer kwargs-eval tripwire landed with a no-side-effect assertion, and role=alert a11y hardening applied to both error containers without touching the frozen success block**

## Performance

- **Duration:** 5 min
- **Started:** 2026-08-06T01:57:39Z
- **Completed:** 2026-08-06T02:03:27Z
- **Tasks:** 2
- **Files modified:** 4 (0 created, 4 modified)

## Accomplishments

- **Form layer joins the shared boundary (SEC-05):** deleted the duplicated `_SAFE_FORMULA_RE` from `forms.py:9` and replaced it with `from .calculations.security import SAFE_FORMULA_RE`; both charset call sites in `clean_reactions` now draw from the single shared compiled regex the engine uses. The drift-guard identity test (`SharedRegexDriftTests`) asserts the exact object — `assertIs(security.SAFE_FORMULA_RE, clean_reactions.__globals__["SAFE_FORMULA_RE"])` and `assertIs(security.is_safe_equation, equilibria.is_safe_equation)` — so either layer forking the regex or predicate fails the suite immediately (T-01-04).
- **Fixed generic error copy (D-06 / CWE-209):** both `JSONDecodeError` branches that echoed attacker-controlled parse-position text now raise/emit the fixed constants — `"Reactions data could not be read."` and `"Concentrations data could not be read."`. `grep -q 'Invalid JSON'` fails on forms.py.
- **View-layer kwargs-path tripwire (TEST-03):** `test_equilibria_view_rejects_kwargs_path_payload` POSTs a 3-segment kwargs-eval payload (`"H2O; x=__import__('os').system('touch /tmp/rce_view_marker_kwargs')"`) hidden in reactants through the full HTTP stack and asserts the form rejects it AND no marker file is created — completing the dual-layer coverage (engine in 01-01, view in 01-02) for both chempy eval paths.
- **a11y error announcements (UI-SPEC):** `role="alert"` added to the reactions-errors container and the result-card failure alert — exactly two attributes (previously zero). The frozen success block (lines 141-215) is byte-identical; the template diff is limited to those two additions.
- **K finite-float (SEC-03) and 5000-char caps (SEC-04) unregressed:** the working-tree K checks, field caps, and equation reconstruction were untouched by the rewire and remain covered by the passing working-tree tests.
- Full suite grew 124 → 128 tests, all green; project source flake8 clean.

## Task Commits

Each task was committed atomically:

1. **Task 1: Form layer joins the shared boundary + drift guard** - `1900c98` (refactor)
2. **Task 2: View-layer kwargs-path tripwire + a11y error announcements** - `243bc0a` (test)

**Plan metadata:** pending final docs commit

## Files Created/Modified

- `apps/chemistry_calculators/forms.py` - MOD — imports `SAFE_FORMULA_RE` from `.calculations.security` (local `_SAFE_FORMULA_RE` deleted); both JSON error branches emit fixed generic copy; K finite-float check, 5000-char caps, equation reconstruction untouched
- `tests/chemistry_calculators/test_security.py` - MOD — added `SharedRegexDriftTests::test_forms_and_engine_share_same_regex` (SEC-05 identity drift guard)
- `tests/chemistry_calculators/test_webapp.py` - MOD — added `test_malformed_reactions_json_generic_copy`, `test_malformed_concentrations_json_generic_copy` (D-06 form copy), `test_equilibria_view_rejects_kwargs_path_payload` (TEST-03 view tripwire)
- `templates/chemistry_calculators/calculator/equilibria.html` - MOD — `role="alert"` on reactions-errors container and result-card failure alert (2-line a11y change)

## Decisions Made

- **Committed the pre-existing working-tree forms.py fix with Task 1:** the uncommitted K finite-float check, 5000-char caps, and charset checks (the phase's load-bearing SEC-03/SEC-04 validation per D-04/D-05) were staged as part of the Task 1 atomic commit — they are the code this plan keeps untouched and had never been separately committed. No behavior change; they were simply never in git.
- **Negative CWE-209 assertions added:** the error-copy tests assert both `assertIn(fixed copy)` and `assertNotIn("Expecting", ...)` — the negative assertion directly enforces "no parser exception text leaks" (D-06 must_haves truth) against a leak variant the plan's assertIn-only test would miss.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Negative leak assertions on form error-copy tests**
- **Found during:** Task 1 (error-copy tests)
- **Issue:** The plan's tests assert the fixed copy is present (`assertIn("...could not be read.")`) but never assert the parser text is absent. A regression that prefixed or wrapped the constant with `str(e)` content would still pass the plan's exact assertions while leaking parse positions.
- **Fix:** Added `assertNotIn("Expecting", form.errors["reactions"][0])` and the concentrations equivalent — the JSONDecodeError parse-position text starts with "Expecting".
- **Files modified:** tests/chemistry_calculators/test_webapp.py
- **Verification:** both new tests green; flake8 clean; grep confirms no 'Invalid JSON' copy path remains in forms.py
- **Committed in:** 1900c98 (Task 1)

---

**Total deviations:** 1 auto-fixed (1 Rule 2 missing critical)
**Impact on plan:** The addition strengthens the D-06/CWE-209 assertion surface without changing any production behavior. No scope creep.

## Issues Encountered

- **`flake8 .` full-repo crash (pre-existing, out of scope):** identical to the 01-01-documented condition — flake8 7.0 does not respect `.gitignore`, so walking `.venv` trips `RecursionError` on sympy's auto-generated `resolvent_lookup.py`. CI runs `flake8 .` inside Docker without `.venv`, so this never blocks CI. Verified project source (`apps tests config manage.py`) is flake8-clean (exit 0).
- **LSP import-resolution noise:** the language server cannot resolve `.venv`-installed packages (django, chempy) — all diagnostics are environment artifacts, not real errors; pytest confirms all imports resolve.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- **Phase 01-close-the-rce complete:** both plans shipped — engine (01-01) and HTTP/form (01-02) halves of the shared no-builtins validation boundary, all five requirements (SEC-03, SEC-04, SEC-05, SEC-06, TEST-03) satisfied, 128 tests green.
- **End-of-phase human-check pending (UAT):** the Task 2 `<verify><human-check>` — load `/equilibria` locally, submit an invalid reaction and confirm the error renders inside a screen-reader-announced alert region, then confirm a valid query (e.g. `HCO3- = H+ + CO3-2; 10**-10.3`) still renders the pH value and species table.
- **Phase 2 (Fix Broken Endpoints)** is the next phase per ROADMAP — `LevelProgressSerializer` misconfiguration (`GET /hplc/api/progress/` 500s), with its endpoint test deferred to Phase 3 per the logged Phase 2 decision.

---

*Phase: 01-close-the-rce*
*Completed: 2026-08-06*

## Self-Check: PASSED

- FOUND: apps/chemistry_calculators/forms.py (SAFE_FORMULA_RE import at line 11; no local regex defs)
- FOUND: tests/chemistry_calculators/test_security.py (SharedRegexDriftTests)
- FOUND: tests/chemistry_calculators/test_webapp.py (3 new tests)
- FOUND: templates/chemistry_calculators/calculator/equilibria.html (exactly 2 role="alert")
- FOUND: 1900c98 (refactor(01-02): rewire form layer to shared security boundary)
- FOUND: 243bc0a (test(01-02): view-layer kwargs tripwire and a11y alert roles)
