---
phase: 01-close-the-rce
verified: 2026-08-08T18:27:51Z
status: passed
score: 12/12 must-haves verified
behavior_unverified: 0
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 11/12
  gaps_closed:
    - "CR-01: unknown-unit concentration → HTTP 200 + fixed copy 'Concentrations data could not be read.' (was HTTP 500) — forms.py guard tuple widened with AttributeError; test_unknown_unit_concentration_rejected + test_equilibria_view_unknown_unit_no_500 green"
    - "WR-01: engine error classification exact — UnsafeEquationError dedicated to validation rejections; genuine solver ValueErrors log logger.exception and return the generic message; test_solver_valueerror_returns_generic_message green (assertLogs ERROR)"
    - "WR-02: is_safe_equation rejects trailing/formula-side newline variants (raw '\\n' guard before split/strip + \\Z anchors) — test_is_safe_equation_rejects_newline_and_non_finite green; empirically re-verified False"
    - "WR-03: form/engine behavioral equivalence — k_value stripped, non-string reactants/products rejected with fixed copy, clean() gates reconstructed equations with the engine's own predicate (drift guard extended to clean.__globals__); 3 regression tests green"
    - "WR-04: engine finite-K gate — safe_k_value evaluates under SAFE_EVAL_GLOBALS + math.isfinite; engine rejects 1e999/1/0 before chempy runs; test_engine_rejects_non_finite_k + safe_k_value unit tests green; empirically re-verified success False"
  gaps_remaining: []
  regressions: []
---

# Phase 1: Close the RCE — Verification Report (Re-verification)

**Phase Goal:** The public equilibria calculator safely rejects untrusted input — chempy parsing is gated by a shared, no-builtins validation boundary that cannot execute attacker code.
**Verified:** 2026-08-08T18:27:51Z
**Status:** passed
**Re-verification:** Yes — after gap closure (01-03 executed: CR-01 + WR-01..WR-04)

> **MVP-mode note (unchanged from prior verification):** ROADMAP marks Phase 1 as `mode: mvp`, but the phase goal is NOT in user-story format (`gsd-tools query user-story.validate` → `false`). The user-flow coverage below is derived from the ROADMAP Success Criteria, which are user-outcome statements, so coverage is still meaningful. If a user-story-formatted goal is required for UAT framing, run `/gsd mvp-phase 1` — flagged for the developer's decision.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A legitimate equilibria query still solves correctly under the restricted globals (SC1 / T1) | ✓ VERIFIED | `test_restricted_globals_still_solves_legit_systems` (pH 7.0), `test_carbonate_example`, `test_simple_acid`, `test_water_autoionization`, `test_ka_mode_engine_solve` (pH 2.88) — green in the 138-pass full suite re-run by the verifier |
| 2 | The engine imports every acceptance rule from the shared security module — no local regex, globals, or predicate definitions remain in equilibria.py (T2) | ✓ VERIFIED | `equilibria.py:11-16` imports from `.security`; `is_safe_equation` identity-checked as the security.py object (drift guard) |
| 3 | EqSystem.from_string is always called with rxn_parse_kwargs globals_ bound to SAFE_EVAL_GLOBALS — SEC-02 pin test green (T3) | ✓ VERIFIED | `test_from_string_always_passes_no_builtins_globals` green in re-run; call site `equilibria.py:125-128` unchanged (D-03 blocker honored); no `globals_=False` code path (only a comment reference) |
| 4 | A reaction string that is not exactly `formula = formula; K` is rejected at the engine boundary by is_safe_equation (T4 / SC2) | ✓ VERIFIED | `SecurityPredicateTests` accept/reject matrix (now 14 rejects incl. newline + non-finite variants) green; engine rejection green |
| 5 | Engine failures return fixed, non-leaking error copy — never exception text (T5) | ✓ VERIFIED | `test_engine_validation_rejection_returns_constant` and `test_engine_solver_failure_returns_generic_message` green; handler split at `equilibria.py:155-179`; `grep str(e)` empty in equilibria.py/forms.py |
| 6 | D-07: Adversarial regression tests cover BOTH chempy eval paths plus non-2-segment and charset violations with no-side-effect checks at the engine layer (T6 / SC3) | ✓ VERIFIED | `test_malicious_k_expr_never_evaluated`, `test_extra_semicolon_parts_rejected` green; param payload rejected: success False, fixed error, no execution |
| 7 | Form and engine import the SAME compiled formula regex object AND the same predicate object — drift-guard identity test passes (T7 / SC4) | ✓ VERIFIED | `SharedRegexDriftTests::test_forms_and_engine_share_same_regex` green — now asserts regex identity + engine predicate identity + `forms.clean.__globals__["is_safe_equation"]` identity (WR-03 extension) |
| 8 | Attacker payloads smuggled through reactants (kwargs-path shape) fail form validation at the view layer with no file created (T8 / SC3) | ✓ VERIFIED | `test_equilibria_view_rejects_kwargs_path_payload` green — form invalid AND no `/tmp/rce_view_marker_kwargs` created |
| 9 | Every user-facing form/result error is a fixed constant — no parser exception text leaks (D-06 / T9) | ✓ VERIFIED | **CR-01 closed:** `forms.py:314` guard now `(json.JSONDecodeError, ValueError, TypeError, AttributeError)` with pint-MRO comment; `test_unknown_unit_concentration_rejected` (fixed copy + CWE-209 `assertNotIn("notAUnit")`) and `test_equilibria_view_unknown_unit_no_500` (explicit `assertEqual(response.status_code, 200)` through the HTTP stack) green in re-run. The unknown-unit input class that previously escaped as HTTP 500 is now a clean form-invalid response with fixed copy |
| 10 | Hidden-field length caps (5000 chars) and finite-K validation remain enforced (T10 / SEC-03, SEC-04) | ✓ VERIFIED | `max_length=5000` on both CharFields (`forms.py:126,133`); `test_k_value_non_numeric_rejected`, `test_k_value_non_finite_rejected`, `test_oversized_reactions_rejected` green |
| 11 | Error announcements are accessible — role=alert on both error containers (T11) | ✓ VERIFIED | `equilibria.html:56` + `:199` — exactly 2 `role="alert"` attributes (grep re-run); template untouched by 01-03 |
| 12 | D-07: Adversarial suite covers both eval paths end-to-end including the view-layer tripwire with no-side-effect assertions (T12 / SC3) | ✓ VERIFIED | Both view tripwires (`test_equilibria_view_rejects_rce_payload`, `test_equilibria_view_rejects_kwargs_path_payload`) green with marker-file assertions |

**Score:** 12/12 truths verified (0 present, behavior-unverified: 0)

### Gap-Closure Verification (01-03 must-haves — all 5 closed)

| # | Truth (01-03) | Status | Evidence |
|---|---------------|--------|----------|
| G1 | POST /equilibria with a concentration whose unit pint does not recognize returns HTTP 200, form invalid, fixed copy 'Concentrations data could not be read.' — never HTTP 500 (CR-01 / D-06) | ✓ VERIFIED | `test_unknown_unit_concentration_rejected` + `test_equilibria_view_unknown_unit_no_500` green (200 asserted); guard tuple `forms.py:314`; pint 0.24.4 MRO comment present |
| G2 | Engine error classification is exact: only UnsafeEquationError returns 'Unsafe or malformed reaction string'; every other exception logs via logger.exception and returns 'The equilibrium system could not be solved.' (WR-01) | ✓ VERIFIED | `UnsafeEquationError(ValueError)` at `equilibria.py:21`, raised at :111, dedicated branch :155; `test_solver_valueerror_returns_generic_message` green incl. `assertLogs` ERROR + "Equilibria calculation failed" in output; `grep 'except ValueError' equilibria.py` → 0 |
| G3 | is_safe_equation rejects trailing-newline variants ('H2O = H+ + OH-; 10**-14\n' is False) and non-finite K expressions ('1e999', '1/0' are False) — the documented acceptance contract is exact as written (WR-02, WR-04) | ✓ VERIFIED | Raw `"\n" in equation` guard is first statement (`security.py:57`, before split/strip — the load-bearing fix); both regexes re-anchored `$` → `\Z` (:18, :21); `test_is_safe_equation_rejects_newline_and_non_finite` green; **verifier empirically re-ran**: trailing/formula-side `\n` → False |
| G4 | Form and engine acceptance rules are behaviorally equivalent: whitespace-padded K values stripped and accepted end-to-end, non-string reactants/products rejected with fixed copy, clean() never emits an equation is_safe_equation rejects (WR-03 / SEC-05) | ✓ VERIFIED | `forms.py:206-213` isinstance checks, :234 `str().strip()` normalization, :281-284 clean() gate with UNSAFE_EQUATION_MESSAGE + early return; `test_k_value_whitespace_padded_accepted` asserts `["H2O = H+ + OH-; 10**-10.3"]`, `test_non_string_reactants_products_rejected`, `test_clean_never_emits_equation_engine_rejects`, extended drift guard — all green |
| G5 | The no-builtins security invariant does not regress: SAFE_EVAL_GLOBALS stays {'__builtins__': {}}, SEC-02 pin green, full suite green, flake8 clean | ✓ VERIFIED | `grep SAFE_EVAL_GLOBALS = {"__builtins__": {}}` → byte-identical match; SEC-02 pin green; `pytest -q` → **138 passed** (verifier re-run, 2.23s); `flake8 apps tests config manage.py` → exit 0; `safe_k_value` evaluates under SAFE_EVAL_GLOBALS (`security.py:43`), never the default context |

### User Flow Coverage (MVP-mode framing)

| Step | Expected | Evidence | Status |
|------|----------|----------|--------|
| Submit a valid equilibria query | Correct pH + species table render (legit chemistry unchanged) | `test_restricted_globals_still_solves_legit_systems` (pH 7.0), `test_ka_mode_engine_solve` (pH 2.88), `test_equilibria_view_post_valid` — green | ✓ |
| Submit a malformed reaction (extra segments, non-numeric/oversized/non-finite K) | Clean validation error; input never evaluated | Predicate matrix (14 rejects) + `test_k_value_non_numeric_rejected` + `test_oversized_reactions_rejected` + `test_engine_rejects_non_finite_k` — green | ✓ |
| Submit an RCE payload (either chempy eval shape) | Form/engine rejects; no file/marker written server-side | `test_malicious_k_expr_never_evaluated`, `test_extra_semicolon_parts_rejected`, `test_equilibria_view_rejects_rce_payload`, `test_equilibria_view_rejects_kwargs_path_payload` — green | ✓ |
| Submit a concentration with an unknown unit | Clean validation error with fixed copy — never 500 | **CR-01 closed:** `test_unknown_unit_concentration_rejected` + `test_equilibria_view_unknown_unit_no_500` (status 200) — green | ✓ |
| Screen reader announces errors | Error text in role=alert region; valid query still renders | 2× `role="alert"` (grep-verified); announcement behavior requires human/browser check — **carried forward to UAT** | ? (human, deferred) |

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | ----------- | ------ | ------- |
| `apps/chemistry_calculators/calculations/security.py` | Shared no-builtins validation boundary: raw-newline guard, `\Z`-anchored regexes, eval globals, predicate, finite-K gate, error constants | ✓ VERIFIED | 73 lines; exports SAFE_FORMULA_RE, SAFE_K_VALUE_RE, SAFE_EVAL_GLOBALS `{"__builtins__": {}}`, UNSAFE_EQUATION_MESSAGE, GENERIC_SOLVER_ERROR_MESSAGE, safe_k_value, is_safe_equation; regex charsets byte-identical to pre-gap versions (anchor-only change) |
| `apps/chemistry_calculators/calculations/equilibria.py` | Engine consumer with UnsafeEquationError split handler | ✓ VERIFIED | Imports 4 names from `.security`; `UnsafeEquationError(ValueError)`; `except UnsafeEquationError` / `except Exception` split; `logger.exception` on generic branch only; `rxn_parse_kwargs={"globals_": SAFE_EVAL_GLOBALS}` untouched |
| `apps/chemistry_calculators/forms.py` | Form layer: AttributeError guard, k_value normalization, str-type checks, clean() is_safe_equation gate | ✓ VERIFIED | Imports `SAFE_FORMULA_RE, UNSAFE_EQUATION_MESSAGE, is_safe_equation`; guard tuple with pint MRO comment; zero local regex/predicate definitions |
| `tests/chemistry_calculators/test_webapp.py` | CR-01/WR-01/WR-03/WR-04 regressions + SEC-02 pin + view tripwires | ✓ VERIFIED | All 12 named tests present and green (verifier re-ran full suite) |
| `tests/chemistry_calculators/test_security.py` | Extended reject matrix + safe_k_value unit tests + drift guard | ✓ VERIFIED | `test_is_safe_equation_rejects_newline_and_non_finite`, `test_safe_k_value_accepts_finite`, `test_safe_k_value_rejects_non_finite`, extended `test_forms_and_engine_share_same_regex` — green |
| `templates/chemistry_calculators/calculator/equilibria.html` | a11y role=alert on both error containers | ✓ VERIFIED | Exactly 2 `role="alert"` (lines 56, 199); unchanged by 01-03 |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `equilibria.py` | `security.py` | `from .security import ...` | ✓ WIRED | Lines 11-16; `is_safe_equation` identity-checked |
| `equilibria.py` from_string call | `security.SAFE_EVAL_GLOBALS` | `rxn_parse_kwargs globals_` identity | ✓ WIRED | Call site `equilibria.py:125-128`; SEC-02 pin asserts identity |
| `equilibria.py` validation loop | `UnsafeEquationError` | `raise UnsafeEquationError(...)` / `except UnsafeEquationError` | ✓ WIRED | Only the validation loop raises it; dedicated branch returns UNSAFE_EQUATION_MESSAGE |
| `is_safe_equation` K gate | `safe_k_value` under SAFE_EVAL_GLOBALS | `eval(k_expr, SAFE_EVAL_GLOBALS)` + `math.isfinite` | ✓ WIRED | `security.py:63` routes through `safe_k_value`; never the default eval context |
| `forms.py` clean() gate | `security.is_safe_equation` | same predicate object the engine applies | ✓ WIRED | `forms.clean.__globals__["is_safe_equation"]` identity asserted by drift guard; imports at `forms.py:12-16` |
| `forms.py` concentrations guard | fixed D-06 copy | `except (... AttributeError)` → add_error | ✓ WIRED | `forms.py:314-326`; pint MRO comment present |
| `equilibria.html` error containers | screen-reader announcement | `role="alert"` | ✓ WIRED | 2 attributes on both error containers |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| `equilibria.py calculate()` | `equilibrium_concentrations` / `ph` | chempy `eqsys.root(init_conc)` → substances/arr | Yes — real solver output asserted by pH tests | ✓ FLOWING |
| `forms.py clean()` concentrations | `result[substance]` | parsed JSON → `Q_(...).to("mol/L")` | Yes — real conversion; unknown units now contained (AttributeError guard) | ✓ FLOWING (CR-01 closed) |
| `forms.py clean_reactions()` | `reactions` list | JSON parse → isinstance/type/strip/K-validity → clean() predicate gate | Yes — real validated data; form cannot emit an equation the engine rejects | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Full suite regression | `pytest -q` (verifier re-run) | **138 passed** in 2.23s | ✓ PASS |
| Lint on project source | `flake8 apps tests config manage.py` | exit 0 | ✓ PASS |
| CR-01: unknown-unit concentrations | `test_equilibria_view_unknown_unit_no_500` (POST through HTTP stack, status 200 asserted) | green | ✓ PASS (was 500) |
| WR-02: trailing newline | `is_safe_equation("H2O = H+ + OH-; 10**-14\n")` — verifier re-ran | False | ✓ PASS (was True) |
| WR-02: formula-side newline | `is_safe_equation("H2O\n = H+ + OH-; 10**-14")` — verifier re-ran | False | ✓ PASS (was True) |
| WR-04: non-finite K at engine | `calculate(["H2O = H+ + OH-; 1e999"])` — verifier re-ran | success False, "Unsafe or malformed reaction string" | ✓ PASS (was success True, pH -0.043) |
| WR-04: zero-division K | `safe_k_value("1/0")` — verifier re-ran | False | ✓ PASS |
| No-invariant regression greps | `SAFE_EVAL_GLOBALS` byte-pin; `globals_=False` (comment-only); `except ValueError` in equilibria.py; `str(e)` in user-facing paths | 1 match / 0 code paths / 0 / 0 | ✓ PASS |
| New-warning probe (advisory) | `is_safe_equation("...10**-14\r")` / `\v` / `\f` | True (accepted) — see Anti-Patterns WR-05; chars excluded by SAFE_K_VALUE_RE/SAFE_FORMULA_RE charsets, chempy splits only on `\n`; not an RCE vector | ℹ️ advisory |

### Probe Execution

Step 7c: SKIPPED — no probe scripts exist for this phase (`scripts/*/tests/probe-*.sh` not found; phase used pytest gates, which were run directly).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ---------- | ----------- | ------ | -------- |
| SEC-01 | 01-01, 01-03 | Reject any reaction string not exactly `formula = formula; K` | ✓ SATISFIED | `SecurityPredicateTests` matrix (14 rejects, hardened in 01-03 with newline + non-finite) + engine reject path |
| SEC-02 | 01-01 | chempy eval locked to `{"__builtins__": {}}`, never `globals_=False`/unset | ✓ SATISFIED | SEC-02 pin test (identity assertIs) green; call site untouched; byte-identical globals grep |
| SEC-03 | 01-02, 01-03 | K values validated finite server-side before interpolation | ✓ SATISFIED | Form float()+isfinite + engine `safe_k_value` (WR-04 hardened both layers); `test_k_value_non_finite_rejected` + `test_engine_rejects_non_finite_k` green |
| SEC-04 | 01-02 | Hidden reactions/concentrations fields length-capped | ✓ SATISFIED | `max_length=5000` both fields; `test_oversized_reactions_rejected` green |
| SEC-05 | 01-01, 01-02, 01-03 | Shared `calculations/security.py` holds regexes/predicate used by both layers | ✓ SATISFIED | security.py exports; both layers import; drift guard now behavioral (regex + predicate identity, incl. `forms.clean.__globals__`) |
| SEC-06 | 01-01, 01-02, 01-03 | Regression tests assert no side effects for payloads on BOTH eval paths | ✓ SATISFIED | Engine + view marker tests green (both paths, both layers) |
| TEST-03 | 01-01, 01-02 | RCE payload tripwires at both engine and view layers | ✓ SATISFIED | Engine tripwires + view tripwires green; no marker files created |

All 7 phase requirement IDs accounted for. REQUIREMENTS.md traceability maps all 7 to Phase 1 with status Complete — consistent with the codebase. No orphaned requirements for Phase 1.

### Anti-Patterns Found

**Closed items (from prior review — all verified fixed):**

| Item | Severity | Closure evidence |
| ---- | -------- | ---------------- |
| CR-01: Uncaught UndefinedUnitError (HTTP 500) | 🛑 Blocker — CLOSED | `forms.py:314` catches AttributeError (pint MRO comment); view test asserts 200 |
| WR-01(prior): ValueError reused for validation + solver, no logging | ⚠️ Warning — CLOSED | UnsafeEquationError split; logger.exception on generic branch; assertLogs test green |
| WR-02(prior): `$` anchor trailing-newline acceptance | ⚠️ Warning — CLOSED | Raw `\n` guard first statement + `\Z` anchors; empirical False |
| WR-03(prior): form/engine behavioral divergence | ⚠️ Warning — CLOSED | strip + isinstance + clean() predicate gate + identity drift guard |
| WR-04(prior): engine-side non-finite K accepted | ⚠️ Warning — CLOSED | `safe_k_value` under SAFE_EVAL_GLOBALS; empirical success False for 1e999 |

**New advisory findings (01-REVIEW.md second pass — none fails a declared must-have truth, none reopens the RCE):**

| File | Line | Pattern | Severity | Impact / Assessment |
| ---- | ---- | ------- | -------- | ------------------- |
| `security.py` | 57 | Raw-line-separator guard checks only `\n` — `\r`/`\v`/`\f` pass the predicate (review WR-01) | ⚠️ Warning (advisory) | Empirically confirmed: `\r`/`\v`/`\f` variants accepted. **Not an RCE vector:** SAFE_K_VALUE_RE and SAFE_FORMULA_RE charsets exclude these characters, chempy 0.9.0 splits only on `"\n"`, and the form layer rejects them via SAFE_FORMULA_RE/float(). The declared 01-03 truth (G3) names `\n` variants specifically — both rejected. Defense-in-depth completeness gap for a hypothetical future `splitlines()`-semantics parser. Recommend `LINE_SEPARATOR_RE` in a future hardening pass |
| `equilibria.py` | 21 | UnsafeEquationError subclasses ValueError, contradicting its own docstring (review WR-02) | ⚠️ Warning (advisory) | Classification works today via except-clause ordering (`except UnsafeEquationError` precedes `except Exception`; verified by the green solver-ValueError test). No current caller catches ValueError around `calculate()` (grep-verified). Risk is future-refactor-only: a future handler adding `except ValueError` would mislabel the validation signal. Recommend base `Exception` if the class is touched again |
| `forms.py` | 297-312 | Concentration dict entries missing `"value"`/`"unit"` keys are silently dropped (review WR-03) | ⚠️ Warning (advisory) | Confirmed by code read: dict-without-both-keys matches no branch, substance silently omitted, form validates clean. Not a declared must-have; the JS UI always sends both keys. Silent-data-loss robustness gap on the API boundary — recommend a validation error for malformed dict entries |

No TBD/FIXME/XXX/PLACEHOLDER markers in any phase-modified file. No stub returns, no hardcoded empty data.

### Human Verification Required

1. **a11y error announcement + valid-query render (deferred from 01-02-PLAN.md Task 2 verify — carried forward to UAT)**
   - **Test:** Load `/equilibria` locally (`python manage.py runserver`), submit an invalid reaction (delete all rows and submit, or enter an invalid K), and confirm the error text renders inside an alert region that a screen reader announces. Then confirm a valid query (e.g. `HCO3- = H+ + CO3-2; 10**-10.3`) still renders the pH value and species table.
   - **Expected:** Error text renders inside the `role="alert"` region; valid query renders the frozen success block unchanged.
   - **Why human:** Screen-reader announcement behavior and visual rendering cannot be verified by grep or unit tests. **Deferred to Phase 1 UAT** — not a blocker for this phase's security goal.

### Gaps Summary

**All five verification gaps from the prior round are CLOSED, each with behavioral evidence:**

1. **CR-01 (blocking)** — the unknown-unit concentration crash is contained. `AttributeError` was added to the concentrations guard (`forms.py:314`) with a pint-MRO comment explaining why (pint 0.24.4 `UndefinedUnitError → AttributeError`), and two regression tests pin it: form-level (`test_unknown_unit_concentration_rejected` with a CWE-209 `assertNotIn("notAUnit")` negative assertion) and view-level (`test_equilibria_view_unknown_unit_no_500` asserting HTTP 200 explicitly — the never-500 assertion). Both green in the verifier's suite re-run.
2. **WR-01** — `UnsafeEquationError` is raised only by the validation loop and caught by a dedicated branch; genuine solver `ValueError`s hit the generic branch, log `logger.exception("Equilibria calculation failed")`, and return the generic fixed copy. Pinned by `test_solver_valueerror_returns_generic_message` (assertLogs at ERROR). No `except ValueError` remains in equilibria.py.
3. **WR-02** — the raw `"\n" in equation` guard is the first statement of `is_safe_equation` (before split/strip — the only fix that can flip the newline cases, since the predicate strips segments before regex matching), with `\Z` anchors as defense-in-depth. Empirically re-verified: trailing and formula-side newlines → False.
4. **WR-03** — form and engine are now behaviorally equivalent: k_value stripped before float()/reconstruction (`" 10.3 "` → `"10**-10.3"` end-to-end, test asserts the cleaned equation), non-string reactants/products rejected with exact per-reaction copy, and `clean()` gates every reconstructed equation with the engine's own predicate object (identity pinned in `clean.__globals__` by the extended drift guard).
5. **WR-04** — `safe_k_value` evaluates K expressions under `SAFE_EVAL_GLOBALS` (the same no-builtins context — no new attack surface) and requires `math.isfinite`; `calculate(["H2O = H+ + OH-; 1e999"])` now returns success False with the validation message (empirically re-verified; was success True with garbage pH).

**No invariant regression:** `SAFE_EVAL_GLOBALS` byte-identical, SEC-02 pin green, no `globals_=False` code path, no `str(e)` in user-facing paths, full suite **138 passed** (verifier re-run), flake8 clean.

**The 3 new advisory warnings from the second-pass review** (`\r`/`\v`/`\f` acceptance, UnsafeEquationError subclassing ValueError, silently-dropped concentration dict keys) are real but none fails a declared must-have truth and none reopens the RCE — each is contained by other layers (charsets, except-ordering with no current ValueError-catching caller, JS-UI always sending both keys). They are documented in the Anti-Patterns section for a future hardening pass.

**Deferred (Step 9b):** None — all phase truths verified; the only outstanding item is the UAT-deferred a11y human check, carried forward above.

**Phase goal status: ACHIEVED.** The public equilibria calculator safely rejects untrusted input: chempy parsing is gated by the shared no-builtins validation boundary (`SAFE_EVAL_GLOBALS` pinned by identity, both eval paths blocked with no side effects), and every input class — including the previously-crashing unknown-unit concentrations — now produces a fixed, non-leaking error response.

---

_Verified: 2026-08-08T18:27:51Z_
_Verifier: the agent (gsd-verifier)_
