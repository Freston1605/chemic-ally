---
phase: 01-close-the-rce
verified: 2026-08-06T02:22:24Z
status: gaps_found
score: 11/12 must-haves verified
behavior_unverified: 0
overrides_applied: 0
gaps:
  - truth: "Every user-facing form/result error is a fixed constant — no parser exception text leaks (D-06)"
    status: failed
    reason: "CR-01 (empirically reproduced end-to-end): pint 0.24.4 UndefinedUnitError subclasses AttributeError (MRO verified), so the except (json.JSONDecodeError, ValueError, TypeError) at forms.py:283 cannot catch it. POST /equilibria with concentrations {\"unit\": \"notAUnit\"} returns HTTP 500 (verified through the full view stack with Client(raise_request_exception=False)), not a clean validation error with fixed copy. The concentrations parse path was designed to contain every parse/conversion failure per D-06; this input class escapes it. The phase's own code review (01-REVIEW.md CR-01) rates this critical and states it violates the D-06/CWE-209 contract."
    artifacts:
      - path: "apps/chemistry_calculators/forms.py"
        issue: "Line 272: q = Q_(float(entry['value']), entry['unit']) — an attacker-controlled unit string raises UndefinedUnitError (AttributeError subclass), which the try/except at lines 260-288 misses. No view-level try/except in BaseCalculateView.post/form_valid contains it either."
    missing:
      - "Broaden the guard: catch (ValueError, TypeError, AttributeError) around the Q_ conversion (or validate the unit up front), add_error with fixed generic copy (e.g. 'Concentrations for X could not be converted to mol/L.')"
      - "Regression test POSTing concentrations {\"unit\": \"notAUnit\"} asserting form invalid + fixed copy (never a 500), mirroring the existing malformed-JSON tests"
  - truth: "Engine error classification and observability (WR-01)"
    status: partial
    reason: "except ValueError at equilibria.py:143 catches both the intentional validation rejection (raise ValueError at line 101) and any solver/parser ValueError raised inside EqSystem.from_string / eqsys.root / dict(zip(...)). A genuine solver ValueError therefore returns the misleading 'Unsafe or malformed reaction string', and the ValueError branch performs no logging — silent swallowing of solver failures a server investigation would need. No security leak (copy is still a fixed constant) but classification + observability are wrong."
    artifacts:
      - path: "apps/chemistry_calculators/calculations/equilibria.py"
        issue: "Lines 100-101 reuse ValueError for the validation signal; the branch at 143-153 never logs."
    missing:
      - "Dedicated UnsafeEquationError(ValueError) exception type raised by the validation loop, caught separately; logger.exception only on the generic branch"
  - truth: "Regex acceptance contract is exact as documented (WR-02)"
    status: partial
    reason: "Both regexes anchor with $ (security.py:14,17); re.match('...$') accepts a single trailing newline. Empirically verified: is_safe_equation('H2O = H+ + OH-; 10**-14\\n') returns True. Inert today (chempy strips newlines) but the documented rejection contract ('Semicolons and any Python syntax are rejected', 'no whitespace/newlines') is false as written and becomes a real injection hole if the charset is ever loosened."
    artifacts:
      - path: "apps/chemistry_calculators/calculations/security.py"
        issue: "Lines 14, 17: $ anchor should be \\Z"
    missing:
      - "\\Z anchors + regression assertions: is_safe_equation('H2O = H+ + OH-; 10**-14\\n') is False"
  - truth: "Engine boundary rejects non-finite K expressions (WR-04)"
    status: partial
    reason: "Empirically verified: calculate(['H2O = H+ + OH-; 1e999']) returns success True with a garbage pH of -0.043 (inf K). The web form blocks it (SEC-03 satisfied at the form tier per the architecture map — float()+isfinite runs before interpolation), so this is a layered-defense gap at the engine boundary, which is the layer this phase hardens. Returns bogus chemistry for inputs the engine's own gate claims to reject."
    artifacts:
      - path: "apps/chemistry_calculators/calculations/security.py"
        issue: "SAFE_K_VALUE_RE is charset-only; no finiteness check on the K expression"
    missing:
      - "Engine-side finite check (evaluate K expr under SAFE_EVAL_GLOBALS, require math.isfinite) mirroring the form's test_k_value_non_finite_rejected"
  - truth: "Form and engine acceptance rules are behaviorally equivalent (WR-03)"
    status: partial
    reason: "The SEC-05 identity drift guard (assertIs) proves pointer-sharing but cannot detect behavioral divergence: k_value=' 10.3 ' (whitespace-padded) passes form float() but the reconstructed '10**- 10.3 ' fails the engine regex → user sees 'Unsafe or malformed reaction string' for a benign typo; non-string JSON types coerce via str() and fail only at chempy pyparsing. The SEC-05 'cannot drift' guarantee is weaker than documented for these classes."
    artifacts:
      - path: "apps/chemistry_calculators/forms.py"
        issue: "K gate float()/isfinite vs engine SAFE_K_VALUE_RE on the reconstructed expression are not equivalent; no isinstance(str) checks on reactants/products"
    missing:
      - "Optional hardening: strip() k_value before float(); reject non-string types; or run is_safe_equation on the reconstructed equation inside clean() so the form can never emit an equation the engine rejects"
human_verification:
  - test: "Load /equilibria locally (python manage.py runserver), submit an invalid reaction (delete all rows and submit, or enter an invalid K), and confirm the error text renders inside an alert region that a screen reader announces. Confirm a valid query (e.g. HCO3- = H+ + CO3-2; 10**-10.3) still renders the pH value and species table."
    expected: "Error text renders inside the role=alert region (announced by screen readers); the valid query renders the frozen success block with pH and species table unchanged."
    why_human: "Screen-reader announcement behavior and visual rendering cannot be verified by grep or unit tests (deferred end-of-phase human-check from 01-02-PLAN.md Task 2 verify)."
---

# Phase 1: Close the RCE — Verification Report

**Phase Goal:** The public equilibria calculator safely rejects untrusted input — chempy parsing is gated by a shared, no-builtins validation boundary that cannot execute attacker code.
**Verified:** 2026-08-06T02:22:24Z
**Status:** gaps_found
**Re-verification:** No — initial verification

> **MVP-mode note (discrepancy surfaced per verify-mvp-mode.md):** ROADMAP marks Phase 1 as `mode: mvp`, but the phase goal is NOT in user-story format (`gsd-tools query user-story.validate` → `false`). The user-flow coverage below is derived from the ROADMAP Success Criteria, which are user-outcome statements, so coverage is still meaningful. If a user-story-formatted goal is required for UAT framing, run `/gsd mvp-phase 1` to reformat — flagged for the developer's decision.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A legitimate equilibria query still solves correctly under the restricted globals (SC1 / T1) | ✓ VERIFIED | `test_restricted_globals_still_solves_legit_systems` (pH 7.0), `test_carbonate_example`, `test_simple_acid`, `test_water_autoionization`, `test_ka_mode_engine_solve` (pH 2.88) — all green in the 128-pass full suite |
| 2 | The engine imports every acceptance rule from the shared security module — no local regex, globals, or predicate definitions remain in equilibria.py (T2) | ✓ VERIFIED | `equilibria.py:11-16` imports from `.security`; `vars(equilibria)` has zero `_SAFE*` locals (executed check); `is_safe_equation` identity-checked as the security.py object |
| 3 | EqSystem.from_string is always called with rxn_parse_kwargs globals_ bound to SAFE_EVAL_GLOBALS — SEC-02 pin test green (T3) | ✓ VERIFIED | `test_from_string_always_passes_no_builtins_globals` green; asserts `kwargs["globals_"] is security.SAFE_EVAL_GLOBALS` (identity, not equality) at `test_webapp.py:455`; call site read at `equilibria.py:113-116` — never `globals_=False` |
| 4 | A reaction string that is not exactly `formula = formula; K` is rejected at the engine boundary by is_safe_equation (T4 / SC2) | ✓ VERIFIED | `SecurityPredicateTests` accept/reject matrix (9 rejects: 0/3 segments, no K, no `=`, empty sides, charset, both eval-path payloads) green; engine rejection green |
| 5 | Engine failures return fixed, non-leaking error copy — never exception text (T5) | ✓ VERIFIED | `test_engine_validation_rejection_returns_constant` and `test_engine_solver_failure_returns_generic_message` green; handler split at `equilibria.py:143-165`; `grep str(e)` empty in equilibria.py/forms.py |
| 6 | D-07: Adversarial regression tests cover BOTH chempy eval paths plus non-2-segment and charset violations with no-side-effect checks at the engine layer (T6 / SC3) | ✓ VERIFIED | `test_malicious_k_expr_never_evaluated`, `test_extra_semicolon_parts_rejected` (marker assertions) green; param payload empirically re-verified: `calculate([...; __import__('os')])` → success False, fixed error, no execution |
| 7 | Form and engine import the SAME compiled formula regex object — drift-guard identity test passes (T7 / SC4) | ✓ VERIFIED | `SharedRegexDriftTests::test_forms_and_engine_share_same_regex` green (`assertIs` on compiled regex + predicate); direct identity checks re-run and passed |
| 8 | Attacker payloads smuggled through reactants (kwargs-path shape) fail form validation at the view layer with no file created (T8 / SC3) | ✓ VERIFIED | `test_equilibria_view_rejects_kwargs_path_payload` green — POSTs 3-segment kwargs payload through the HTTP stack, asserts form invalid AND no `/tmp/rce_view_marker_kwargs` created |
| 9 | Every user-facing form/result error is a fixed constant — no parser exception text leaks (D-06 / T9) | ✗ FAILED | **CR-01:** `UndefinedUnitError` escapes `EquilibriumSystemForm.clean()` (forms.py:272) — pint 0.24.4 MRO `UndefinedUnitError → AttributeError → PintError → Exception` is not covered by `except (json.JSONDecodeError, ValueError, TypeError)`. Reproduced: POST /equilibria with `{"unit": "notAUnit"}` → **HTTP 500** through the full view stack. The input is not cleanly rejected; the request crashes. No exception text leaks in production (DEBUG=False) but the D-06 containment contract is violated. |
| 10 | Hidden-field length caps (5000 chars) and finite-K validation remain enforced (T10 / SEC-03, SEC-04) | ✓ VERIFIED | `max_length=5000` on both CharFields (`forms.py:121,128`); `test_k_value_non_numeric_rejected`, `test_k_value_non_finite_rejected`, `test_oversized_reactions_rejected` green |
| 11 | Error announcements are accessible — role=alert on both error containers (T11) | ✓ VERIFIED | `equilibria.html:56` (`<div role="alert">` wrapping reactions-errors loop) and `:199` (`ca-alert-danger role="alert"`) — exactly 2 attributes; frozen success block untouched |
| 12 | D-07: Adversarial suite covers both eval paths end-to-end including the view-layer tripwire with no-side-effect assertions (T12 / SC3) | ✓ VERIFIED | Both view tripwires (`test_equilibria_view_rejects_rce_payload`, `test_equilibria_view_rejects_kwargs_path_payload`) green with marker-file assertions; dual-layer coverage complete |

**Score:** 11/12 truths verified (1 present, behavior-unverified: 0)

### User Flow Coverage (MVP-mode framing)

| Step | Expected | Evidence | Status |
|------|----------|----------|--------|
| Submit a valid equilibria query | Correct pH + species table render (legit chemistry unchanged) | `test_restricted_globals_still_solves_legit_systems` (pH 7.0), `test_ka_mode_engine_solve` (pH 2.88), `test_equilibria_view_post_valid` — green | ✓ |
| Submit a malformed reaction (extra segments, non-numeric/oversized K) | Clean validation error; input never evaluated | Predicate matrix + `test_k_value_non_numeric_rejected` + `test_oversized_reactions_rejected` + `test_equilibria_view_post_invalid` — green | ✓ |
| Submit an RCE payload (either chempy eval shape) | Form/engine rejects; no file/marker written server-side | `test_malicious_k_expr_never_evaluated`, `test_extra_semicolon_parts_rejected`, `test_equilibria_view_rejects_rce_payload`, `test_equilibria_view_rejects_kwargs_path_payload` — green | ✓ |
| Submit a concentration with an unknown unit | Clean validation error with fixed copy | ✗ FAILED — HTTP 500 (CR-01), no regression test exists | ✗ |
| Screen reader announces errors | Error text in role=alert region; valid query still renders | 2× `role="alert"` in template (grep-verified); announcement behavior requires human/browser check | ? (human) |

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | ----------- | ------ | ------- |
| `apps/chemistry_calculators/calculations/security.py` | Shared no-builtins validation boundary (regexes, eval globals, predicate, error constants) | ✓ VERIFIED | Exists (43 lines), exports all 6 symbols: SAFE_FORMULA_RE, SAFE_K_VALUE_RE, SAFE_EVAL_GLOBALS `{"__builtins__": {}}`, UNSAFE_EQUATION_MESSAGE, GENERIC_SOLVER_ERROR_MESSAGE, is_safe_equation |
| `apps/chemistry_calculators/calculations/equilibria.py` | Engine consumer of shared boundary with differentiated error handler | ✓ VERIFIED | Imports 4 names from `.security`; `rxn_parse_kwargs={"globals_": SAFE_EVAL_GLOBALS}` at call site; split ValueError/Exception handler; no local regex/globals/predicate |
| `apps/chemistry_calculators/forms.py` | Form layer consuming SAFE_FORMULA_RE; generic JSON error copy | ✓ VERIFIED | `from .calculations.security import SAFE_FORMULA_RE` (line 11); zero local regex defs; both JSON branches emit fixed copy; K finite-float + 5000-char caps intact |
| `tests/chemistry_calculators/test_webapp.py` | SEC-02 pin test, engine error-copy, Ka-mode, view tripwires | ✓ VERIFIED | All named tests present and green (19 security tests in one run); imports `patch` + `security` |
| `tests/chemistry_calculators/test_security.py` | SecurityPredicateTests (SEC-01 matrix) + SharedRegexDriftTests (SEC-05) | ✓ VERIFIED | Both classes present; accept/reject tables match plan exactly; identity drift guard green |
| `templates/chemistry_calculators/calculator/equilibria.html` | a11y role=alert on both error containers | ✓ VERIFIED | Exactly 2 `role="alert"` (lines 56, 199); success block untouched |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `equilibria.py` | `security.py` | `from .security import ...` | ✓ WIRED | Line 11-16; `is_safe_equation` identity-checked |
| `equilibria.py` from_string call | `security.SAFE_EVAL_GLOBALS` | `rxn_parse_kwargs globals_` identity | ✓ WIRED | Call site `equilibria.py:113-116`; SEC-02 pin test asserts `is security.SAFE_EVAL_GLOBALS` |
| `forms.py` | `security.py` | `from .calculations.security import SAFE_FORMULA_RE` | ✓ WIRED | Line 11; drift guard `assertIs` green; direct identity check re-run |
| `equilibria.html` error containers | screen-reader announcement | `role="alert"` | ✓ WIRED | 2 attributes on both error containers |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| `equilibria.py calculate()` | `equilibrium_concentrations` / `ph` | chempy `eqsys.root(init_conc)` → substances/arr | Yes — real solver output asserted by pH tests | ✓ FLOWING |
| `forms.py clean()` concentrations | `result[substance]` | parsed JSON → `Q_(...).to("mol/L")` | Yes — real conversion; **but unknown unit escapes as 500 (CR-01)** | ⚠️ HOLLOW-path (CR-01) |
| `forms.py clean_reactions()` | `reactions` list | JSON parse → per-reaction charset/K validation | Yes — real validated data | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| SEC-02 globals pin + error copy + Ka-mode + drift guard + tripwires + predicate matrix (19 named tests) | `pytest test_webapp.py test_security.py -k "<19 names>"` | 19 passed | ✓ PASS |
| Full suite regression | `pytest -q` | 128 passed (baseline 118 → 124 → 128) | ✓ PASS |
| Lint on phase files | `flake8` on 5 modified files | exit 0 | ✓ PASS |
| Param-path payload rejected at engine | `calculate(["H2O = H+ + OH-; __import__('os')"])` | success False, fixed error copy | ✓ PASS |
| CR-01: unknown-unit concentrations | `POST /equilibria` `{"unit": "notAUnit"}` via `Client(raise_request_exception=False)` | **HTTP 500** (UndefinedUnitError escapes) | ✗ FAIL |
| WR-02: trailing newline | `is_safe_equation("H2O = H+ + OH-; 10**-14\n")` | True (accepted) | ✗ FAIL (contract) |
| WR-04: non-finite K at engine | `calculate(["H2O = H+ + OH-; 1e999"])` | success True, pH -0.043 (garbage) | ✗ FAIL (defense-in-depth) |
| No `str(e)` in user-facing paths | `grep str(e) equilibria.py forms.py` | no matches | ✓ PASS |
| No local defs in engine | `vars(equilibria)` `_SAFE*` scan | none | ✓ PASS |
| Identity (form/engine share objects) | direct `assertIs` checks | all pass | ✓ PASS |

### Probe Execution

Step 7c: SKIPPED — no probe scripts exist for this phase (`scripts/*/tests/probe-*.sh` not found; phase used pytest gates, which were run directly).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ---------- | ----------- | ------ | -------- |
| SEC-01 | 01-01 | Reject any reaction string not exactly `formula = formula; K` | ✓ SATISFIED | `SecurityPredicateTests` matrix + engine reject path |
| SEC-02 | 01-01 | chempy eval locked to `{"__builtins__": {}}`, never `globals_=False`/unset | ✓ SATISFIED | SEC-02 pin test (identity assertIs) + call-site read |
| SEC-03 | 01-02 | K values validated finite server-side before interpolation | ✓ SATISFIED | `forms.py:217-226` float()+isfinite; `test_k_value_non_finite_rejected` green |
| SEC-04 | 01-02 | Hidden reactions/concentrations fields length-capped | ✓ SATISFIED | `max_length=5000` both fields; `test_oversized_reactions_rejected` green |
| SEC-05 | 01-01, 01-02 | Shared `calculations/security.py` holds regexes used by both layers | ✓ SATISFIED | security.py exports; both layers import; drift-guard identity test green |
| SEC-06 | 01-01, 01-02 | Regression tests assert no side effects for payloads on BOTH eval paths | ✓ SATISFIED | Engine + view marker tests green (both paths, both layers) |
| TEST-03 | 01-01, 01-02 | RCE payload tripwires at both engine and view layers | ✓ SATISFIED | Engine tripwires + view tripwires green; no marker files created |

All 7 phase requirement IDs accounted for. REQUIREMENTS.md traceability maps all 7 to Phase 1 with status Complete — consistent with the codebase. No orphaned requirements for Phase 1.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `apps/chemistry_calculators/forms.py` | 272 | Uncaught exception class (UndefinedUnitError/AttributeError escapes a try designed to contain all parse/conversion failures) | 🛑 Blocker (CR-01) | Attacker-triggerable HTTP 500 on public /equilibria; violates D-06 must-have truth |
| `apps/chemistry_calculators/calculations/equilibria.py` | 100-101, 143 | ValueError reused for validation signal and solver failures; validation branch never logs | ⚠️ Warning (WR-01) | Misleading error classification; silent swallow of solver failures |
| `apps/chemistry_calculators/calculations/security.py` | 14, 17 | `$` anchor accepts single trailing newline | ⚠️ Warning (WR-02) | Rejection contract false as written; inert today (chempy strips newlines) |
| `apps/chemistry_calculators/calculations/security.py` | 17 | K gate charset-only; non-finite K accepted at engine | ⚠️ Warning (WR-04) | Engine returns garbage chemistry for `1e999` (form blocks it) |
| `apps/chemistry_calculators/forms.py` | 217-226 vs security.py:17 | Form/engine K gates behaviorally divergent (whitespace-padded K, non-string coercion) | ⚠️ Warning (WR-03) | Benign typos rejected as 'unsafe'; SEC-05 guarantee weaker than documented |

No TBD/FIXME/XXX/PLACEHOLDER markers in any phase-modified file. No stub returns, no hardcoded empty data.

### Human Verification Required

1. **a11y error announcement + valid-query render (deferred from 01-02-PLAN.md Task 2 verify)**
   - **Test:** Load `/equilibria` locally (`python manage.py runserver`), submit an invalid reaction (delete all rows and submit, or enter an invalid K), and confirm the error text renders inside an alert region that a screen reader announces. Then confirm a valid query (e.g. `HCO3- = H+ + CO3-2; 10**-10.3`) still renders the pH value and species table.
   - **Expected:** Error text renders inside the `role="alert"` region; valid query renders the frozen success block unchanged.
   - **Why human:** Screen-reader announcement behavior and visual rendering cannot be verified by grep or unit tests.

### Gaps Summary

**The RCE closure — the phase's core security invariant — is complete and behaviorally verified.** The `SAFE_EVAL_GLOBALS` (`{"__builtins__": {}}`) contract is pinned at the `from_string` call site by a mock identity test (SEC-02); both chempy eval paths (param + kwargs) are blocked with no side effects at both engine and view layers; the shared `security.py` boundary prevents layer drift; legitimate chemistry still solves (128 tests green, flake8 clean).

**The phase goal is not fully achieved, however.** The goal's first clause — "the public equilibria calculator **safely rejects** untrusted input" — fails for one input class: concentrations carrying an unknown unit (CR-01). `UndefinedUnitError` (an `AttributeError` subclass in pint 0.24.4) escapes `EquilibriumSystemForm.clean()` because the guard catches only `(json.JSONDecodeError, ValueError, TypeError)`, producing an attacker-triggerable **HTTP 500** on the public endpoint (reproduced end-to-end) instead of a clean validation error with fixed copy. This directly fails must-have truth T9 (D-06 error-copy contract) and was independently rated **critical** by the phase's own code review (01-REVIEW.md CR-01, which explicitly notes the D-06 test suite only covers malformed JSON, never malformed units).

**CR-01 does NOT reopen the RCE** — the payload never reaches chempy's eval with attacker code, and no exception text leaks in production (DEBUG=False). It is an availability + error-containment defect on the same public endpoint the phase hardens.

Secondary findings (WR-01..WR-04) are warnings: they weaken documented contracts or defense-in-depth (mislabeled engine errors, trailing-newline acceptance, engine-side non-finite K, form/engine behavioral divergence) but do not fail a declared must-have truth. They should be scheduled for closure with CR-01 rather than shipped silently.

**Deferred (Step 9b):** None — Phase 2 (Fix Broken Endpoints: progress endpoint, reaction balancing, SimulateView error bodies) does not specifically cover the equilibria concentrations-unit crash or the engine K-finiteness gap. Conservative match rule applied; CR-01 remains a real gap requiring a closure plan.

**Recommended closure plan:** (1) widen the concentrations-conversion guard to catch `AttributeError` (or validate units up front) with fixed generic copy; (2) add a regression test POSTing `{"unit": "notAUnit"}` asserting form-invalid + fixed copy (never 500); (3) optionally fold in WR-01 (dedicated `UnsafeEquationError`), WR-02 (`\Z` anchors), WR-04 (engine-side finite-K check) while the security module is open. The RCE invariant itself needs no change.

---

_Verified: 2026-08-06T02:22:24Z_
_Verifier: the agent (gsd-verifier)_
