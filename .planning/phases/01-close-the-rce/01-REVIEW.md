---
phase: 01-close-the-rce
reviewed: 2026-08-08T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - apps/chemistry_calculators/calculations/security.py
  - apps/chemistry_calculators/calculations/equilibria.py
  - apps/chemistry_calculators/forms.py
  - tests/chemistry_calculators/test_webapp.py
  - tests/chemistry_calculators/test_security.py
  - templates/chemistry_calculators/calculator/equilibria.html
findings:
  critical: 0
  warning: 3
  info: 3
  total: 6
status: issues_found
---

# Phase 1: Code Review Report (second pass — gap-closure verification)

**Reviewed:** 2026-08-08
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Second review of the RCE-hardening phase. All five mandated gap-closure fixes were verified correct and complete, with empirical evidence (live exploit simulation against the installed chempy 0.9.0 / pint 0.24.4 stack, plus the full test suite: **78/78 tests pass**):

1. **AttributeError catch in concentrations guard (CR-01)** — CORRECT and necessary. Verified pint 0.24.4 `UndefinedUnitError.__mro__` = `[UndefinedUnitError, AttributeError, PintError, Exception, ...]`; the catch tuple `(JSONDecodeError, ValueError, TypeError, AttributeError)` also covers `DimensionalityError` (subclasses `PintTypeError` → `TypeError`), `OffsetUnitCalculusError`-family, and empty-string units — every Q_ failure mode I probed (`meter`, `s`, `kg`, `K`, `liter`, `mL`, `notAUnit`, `"1.5"`, `""`) is contained.
2. **UnsafeEquationError split (WR-01)** — behaviorally correct: `except UnsafeEquationError` precedes `except Exception`, genuine solver `ValueError` reaches the generic branch and logs (test `test_solver_valueerror_returns_generic_message` + `assertLogs` pass). One design caveat, see WR-02.
3. **Raw newline guard + \Z anchors (WR-02)** — CORRECT for `\n`. Guard is the first statement; empirical probe confirms `"H2O = H+ + OH-; 10**-14\n"` and `"H2O\n= H+ + OH-; __import__('os')..."` are rejected. Caveat for `\r`/`\v`/`\f`, see WR-01.
4. **safe_k_value finite check under SAFE_EVAL_GLOBALS (WR-04)** — CORRECT. `1e999` → inf → rejected; `1/0` → ZeroDivisionError → rejected; the chempy kwargs path `eval("dict(...)", SAFE_EVAL_GLOBALS)` raises NameError on `dict` (verified) so it is dead even before the 2-segment rule; the SEC-02 pin test asserts the exact globals object reaches `from_string`.
5. **Form/engine behavioral equivalence (WR-03)** — CORRECT. Identity tests (`assertIs` on `SAFE_FORMULA_RE` and `is_safe_equation` through `__globals__`), k_value strip-then-float normalization, isinstance fast-fails, and the clean() predicate gate all hold; the form cannot emit an equation the engine rejects.

**RCE closure:** 4 live payloads (newline-injection, 3-segment kwargs, print, formula-side newline) executed against the real engine — all return `success: False`, zero marker files created. No blockers remain.

Three warnings and three info items below are robustness/completeness issues, none of which reopen the RCE.

## Warnings

### WR-01: Raw-line-separator guard only checks `\n` — `\r`, `\v`, `\f` pass the predicate

**File:** `apps/chemistry_calculators/calculations/security.py:57`
**Issue:** The guard rejects only `"\n"`, but the docstring's rationale applies identically to every other line separator: the predicate `.strip()`s each `;`-segment *before* regex matching, and `str.strip()` erases trailing `\r`/`\v`/`\f`, so `\Z` anchors alone cannot reject them. Empirically verified:
- `is_safe_equation("H2O\r= H+ + OH-; 10**-14")` → **True**
- `is_safe_equation("H2O = H+ + OH-; 10**-14\r")` → **True** (`\v`, `\f` same)
- The engine then solves those equations successfully (chempy's formula parser silently strips `\r`; pH 6.13 == control).

This is **not** currently an RCE vector — the K-segment charset and SAFE_FORMULA_RE both exclude these characters internally, chempy 0.9.0 splits only on `"\n"`, and the form layer rejects `\r` via SAFE_FORMULA_RE/`float()` — but the predicate's documented contract ("reject raw line separators before strip/split") is incomplete, and the moment chempy (or any downstream parser) switches to `str.splitlines()` semantics (`\r`, `\v`, `\f`, `\x1c`–`\x1e`, `\x85`, `\u2028`, `\u2029` are all Python line boundaries), this gap becomes a line-injection primitive. The defense-in-depth layer is exactly where this must be airtight.

**Fix:**
```python
# security.py, module level
LINE_SEPARATOR_RE = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")

def is_safe_equation(equation: str) -> bool:
    if LINE_SEPARATOR_RE.search(equation):
        return False
    ...
```

### WR-02: `UnsafeEquationError` subclasses `ValueError`, contradicting its own classification contract

**File:** `apps/chemistry_calculators/calculations/equilibria.py:21`
**Issue:** The class docstring says "Distinct from solver/parser ValueErrors so the handler can classify the validation-rejection signal separately" — but it inherits from `ValueError`, so a bare `except ValueError` catches *both* the validation signal and genuine solver ValueErrors. The split currently works only because of except-clause *ordering* inside `calculate()` and because the sole caller (`CalculateEquilibriaView.process_calculation`, verified by grep) never catches ValueError. Any future handler (DRF API wrapper, refactored view, session util) that adds `except ValueError` around `calculate()` will silently mislabel `UnsafeEquationError` as a solver failure — the exact misclassification WR-01 was meant to eliminate.

**Fix:**
```python
class UnsafeEquationError(Exception):
    """Raised when a reaction string fails the shared is_safe_equation gate.
    ...
    """
```
(No other code catches `ValueError` from `calculate()` today; the `except UnsafeEquationError` branch inside `calculate()` is unaffected.)

### WR-03: Concentration dict entries missing `"value"` or `"unit"` keys are silently dropped

**File:** `apps/chemistry_calculators/forms.py:297-312`
**Issue:** An entry like `{"H2O": {"value": 55.4}}` (missing `"unit"`) or `{"H2O": {"unit": "mol/L"}}` (missing `"value"`) matches none of the three branches (dict-with-both-keys / number / string), so the substance is silently omitted from `cleaned_data["concentrations"]` while the form validates **clean**. The engine's `defaultdict(float, ...)` then gives the substance concentration 0.0, and the user gets a plausible-looking but wrong result with no error — silent data loss in the exact code path this phase hardened. (The JS UI always sends both keys, so this is an API-boundary robustness gap, not a UI bug.)

**Fix:** treat a dict entry lacking either key as a validation error (or a default unit):
```python
if isinstance(entry, dict) and "value" in entry and "unit" in entry:
    ...
elif isinstance(entry, dict):
    self.add_error("concentrations", f"Entry for '{substance}' must have 'value' and 'unit'.")
    continue
```

## Info

### IN-01: Redundant `str()` coercion after isinstance checks

**File:** `apps/chemistry_calculators/forms.py:218,222`
**Issue:** `rxn["reactants"]`/`rxn["products"]` are already proven `str` by the isinstance checks at lines 206-213, so `not str(rxn["reactants"]).strip()` is dead coercion. Harmless; drop the `str()` wrapper for clarity.

### IN-02: `solvent` field has no charset gate

**File:** `apps/chemistry_calculators/forms.py:138-145`
**Issue:** `solvent` (max_length=20) is the only user-controlled string on this form with zero charset validation, while reactants/products/K got full gates. Traced the flow: it lands in `init_conc[solvent]` as a dict key only — never in an eval or chempy parse — so this is not a vector. It is, however, inconsistent with the SEC-05 "single boundary" posture; consider gating it with `SAFE_FORMULA_RE` for uniformity (note `AgCl(s)`-style entries with parens are already allowed by that charset).

### IN-03: Non-finite concentration values pass form validation

**File:** `apps/chemistry_calculators/forms.py:303,310`
**Issue:** `{"value": "1e999", "unit": "mol/L"}` → `inf`, and `{"value": "NaN"}` → `nan`, flow into `cleaned_data["concentrations"]` — the isfinite check was applied to K values (WR-04) but not to concentration values. Contained today (solver fails or emits non-finite pH, template renders it as `nan`/`inf` rather than an error), but it violates the phase's own "reject non-finite before the solver" standard. Add `math.isfinite` after `float(entry["value"])` and after the string branch.

## Verified-closed items from the prior review

- **CR-01** (pint UndefinedUnitError → HTTP 500): closed; MRO verified, view-level test `test_equilibria_view_unknown_unit_no_500` passes.
- **WR-01** (error classification): closed; exception-split behavior pinned by tests; see WR-02 for the subclassing caveat.
- **WR-02** (trailing/formula newline): closed for `\n`; see WR-01 for the `\r`/`\v`/`\f` completeness gap.
- **WR-03** (form/engine equivalence): closed; identity + behavioral gates verified.
- **WR-04** (non-finite K): closed; `1e999`, `1/0` rejected at both form and engine boundaries.
- **Info items** (fixed copy D-06, CWE-209 assertions): closed; `assertNotIn` leak checks pass.

---

_Reviewed: 2026-08-08_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
