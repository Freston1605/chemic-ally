---
phase: 01-close-the-rce
reviewed: 2026-08-05T18:30:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - apps/chemistry_calculators/calculations/security.py
  - tests/chemistry_calculators/test_security.py
  - apps/chemistry_calculators/calculations/equilibria.py
  - tests/chemistry_calculators/test_webapp.py
  - apps/chemistry_calculators/forms.py
  - templates/chemistry_calculators/calculator/equilibria.html
findings:
  critical: 1
  warning: 4
  info: 2
  total: 7
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-08-05T18:30:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Security-hardening phase closing the chempy `eval()` RCE. The core eval contract is **sound and verified**:

- `SAFE_EVAL_GLOBALS = {"__builtins__": {}}` genuinely blocks builtins in chempy's `to_reaction` eval (parsing.py:504) — `__import__` raises `NameError` under it, while arithmetic literals like `10**-14/55.4` still evaluate (empirically verified against the installed chempy 0.6.x).
- The 3-segment kwargs-eval path (parsing.py:491, `eval("dict(...)")`) is unreachable: `is_safe_equation` requires exactly 2 `;` segments, and even if reached, `dict` is not resolvable under the empty builtins dict. The claim in `security.py:19-20` about `globals_=False` exposing builtins via `{}` is accurate.
- `Equilibrium._str_arrow = "="` matches the predicate's `=` requirement; the formula side reaches only chempy's pyparsing formula parser (no eval).
- Error-copy hygiene holds: both the ValueError branch and the generic branch return fixed constants; `str(e)`/exception text never reaches the response (the `test_malformed_*_json_generic_copy` tests pin this).
- The SEC-05 identity drift guard (`assertIs` on the shared regex/predicate) works.

However, the boundary has one real crash hole, several validation-layering gaps, and regex/anchor defects, detailed below.

## Critical Issues

### CR-01: Uncaught `UndefinedUnitError` escapes `clean()` — attacker-triggerable 500

**File:** `apps/chemistry_calculators/forms.py:272` (with except at 283-288)
**Issue:** In the concentrations parse, `Q_(float(entry["value"]), entry["unit"])` runs with an attacker-controlled `unit` string. Pint raises `UndefinedUnitError` for an unknown unit (e.g. `"notAUnit"`). `UndefinedUnitError` subclasses `AttributeError`, **not** `ValueError`/`TypeError` (verified: MRO `UndefinedUnitError → AttributeError → PintError → Exception`), so it is **not caught** by `except (json.JSONDecodeError, ValueError, TypeError)`. It propagates out of `clean()`, through `form.is_valid()` in `BaseCalculateView.post`, with no handler → HTTP 500 on a public endpoint. Reproduced:

```
POST {"reactions": [...], "concentrations": {"H2O": {"value": 55.4, "unit": "notAUnit"}}}
→ UNCAUGHT UndefinedUnitError: 'notAUnit' is not defined in the unit registry
```

This is exactly the class of unhandled conversion failure the phase's error-copy contract (D-06/CWE-209) was meant to contain — the `try` was designed to catch every parse/conversion failure and misses this one. The D-06 test suite only covers malformed JSON, never malformed units.

**Fix:** Broaden the guard and/or validate the unit up front:

```python
elif isinstance(entry, dict) and "value" in entry and "unit" in entry:
    try:
        q = Q_(float(entry["value"]), entry["unit"])
        result[substance] = float(q.to("mol/L").magnitude)
    except (ValueError, TypeError, AttributeError):
        # pint UndefinedUnitError subclasses AttributeError
        self.add_error(
            "concentrations",
            f"Concentrations for '{substance}' could not be converted to mol/L.",
        )
```

(Also add a regression test posting `{"unit": "notAUnit"}` and asserting `is_valid()` returns `False` with the generic copy rather than raising.)

## Warnings

### WR-01: `except ValueError` conflates validation rejection with solver/parser failures and never logs

**File:** `apps/chemistry_calculators/calculations/equilibria.py:100-101, 143-153`
**Issue:** The `raise ValueError(...)` at line 101 (the intended validation-rejection signal) is caught by the same `except ValueError` that also catches **any** `ValueError` raised inside `EqSystem.from_string`, `eqsys.root()`, or `dict(zip(...))`. A genuine solver failure therefore returns the fixed "Unsafe or malformed reaction string" — a false accusation about the user's input — and, unlike the generic branch, the ValueError branch performs **no logging**, silently swallowing real solver/parser failures that a server-side investigation would need. Verified: an equation that passes the predicate but breaks chempy's formula parser (e.g. numeric reactants) surfaces as the generic solver message today only because pyparsing raises `ParseException` (not `ValueError`) — but any chempy/scipy `ValueError` takes the mislabeled path.

**Fix:** Give the validation rejection its own exception type so it cannot collide with solver errors:

```python
class UnsafeEquationError(ValueError):
    pass
...
raise UnsafeEquationError(...)
...
except UnsafeEquationError:
    return {...UNSAFE_EQUATION_MESSAGE...}
except Exception:
    logger.exception("Equilibria calculation failed")
    return {...GENERIC_SOLVER_ERROR_MESSAGE...}
```

### WR-02: Both regexes accept a single trailing newline (`$` vs `\Z`)

**File:** `apps/chemistry_calculators/calculations/security.py:14, 17`
**Issue:** `re.match(r"^[...]+$", ...)` — Python's `$` matches before a **final** newline, so `SAFE_K_VALUE_RE.match("10**-14\n")` and `SAFE_FORMULA_RE.match("H2O\n")` both return a match (empirically verified). The gate is documented as rejecting whitespace/newlines ("no Python syntax", "Semicolons and any Python syntax are rejected") but one trailing newline slips through, and `is_safe_equation` then admits the equation end-to-end (engine returns `success: True` for `"H2O = H+ + OH-; 10**-14\n"`). A second newline is correctly rejected, so today the leak is inert (chempy `rstrip("\n")`/`strip()` neutralizes it), but the acceptance contract is false as written and becomes a real injection hole if the charset is ever loosened. This is precisely the "regex correctness for formula/K validation" focus area.

**Fix:** Anchor with `\Z`:

```python
SAFE_FORMULA_RE = re.compile(r"^[A-Za-z0-9+\-() .\u00b7]+\Z")
SAFE_K_VALUE_RE = re.compile(r"^[0-9.eE+\-*/()]+\Z")
```

Add regression assertions: `is_safe_equation("H2O = H+ + OH-; 10**-14\n") is False` and `is_safe_equation("H2O\n = H+ + OH-; 10**-14") is False`.

### WR-03: Form and engine acceptance rules drift behaviorally despite the SEC-05 identity guard

**File:** `apps/chemistry_calculators/forms.py:217-226, 248-251`; drift guard at `tests/chemistry_calculators/test_security.py:55-70`
**Issue:** The form's K-value gate is `float(rxn["k_value"])` + `math.isfinite`, but the engine gate is `SAFE_K_VALUE_RE` applied to the *reconstructed* expression `10**-{k_value}`. These are not equivalent:

- `k_value = " 10.3 "` (whitespace-padded): `float()` accepts → form **valid** → equation becomes `"H2O = H+ + OH-; 10**- 10.3 "` → engine predicate **rejects** (space not in charset) → user sees "Unsafe or malformed reaction string" for a benign typo.
- `k_value = "+10.3"` similarly passes `float()` but yields `"10**-+10.3"` (accepted, but syntactically odd).
- Non-string JSON types pass via `str()` coercion: `{"reactants": 123}` → `"123 = H+ + OH-; 10**-14.0"` → form valid, engine predicate valid, then chempy's pyparsing grammar fails with a `ParseException` → generic solver error (verified).

The `SharedRegexDriftTests` guard only asserts **object identity** of the regex/predicate — it cannot detect this behavioral divergence, so the SEC-05 guarantee ("the layers cannot drift apart") is weaker than documented.

**Fix:** (a) Reject non-string types explicitly (`isinstance(rxn["reactants"], str)` etc.); (b) `strip()` k_value before `float()`; (c) strongest: run `is_safe_equation` on the reconstructed equation inside `clean_reactions`/`clean()` so the form can never emit an equation the engine will reject — that makes the two layers behaviorally identical, not just pointer-identical.

### WR-04: Engine accepts non-finite / zero-division K expressions that the form rejects

**File:** `apps/chemistry_calculators/calculations/equilibria.py:99-102` with `security.py:17`
**Issue:** `is_safe_equation` is the "single source of truth" gate for what reaches chempy's eval, but it accepts `"1e999"` (→ `inf`), `"1e-999"` (→ `0.0`), and `"1/0"` (→ `ZeroDivisionError` at eval). Verified: `calculate(["H2O = H+ + OH-; 1e999"])` returns **`success: True` with a garbage pH of -0.04**; `"1/0"` raises `ZeroDivisionError` inside chempy's eval, producing a full traceback log and the generic solver message. The web form blocks all three (`test_k_value_non_finite_rejected` covers only the form), so today this is a layered-defense gap rather than an exploit — but the engine is the boundary the phase is hardening, and it returns bogus chemistry for inputs its own documented contract claims to reject.

**Fix:** Add an engine-side finite check. Since the K expression is eval'd by chempy anyway, evaluate it once in the gate with the same restricted globals and require a finite result:

```python
def safe_k_value(k_expr: str) -> bool:
    if not SAFE_K_VALUE_RE.match(k_expr):
        return False
    try:
        return math.isfinite(eval(k_expr, SAFE_EVAL_GLOBALS))  # restricted globals only
    except Exception:
        return False
```

(Use `SAFE_EVAL_GLOBALS`, never the default context; this reuses the documented threat model rather than adding a new one.) Add an engine test mirroring the form's `test_k_value_non_finite_rejected`.

## Info

### IN-01: `views.py:430` splits on bare `"+"`, polluting session autocomplete

**File:** `apps/chemistry_calculators/views.py:427-435`
**Issue:** `side.split("+")` on the success path splits charge notation: `"H+"` → `"H"`, `"Ca+2"` → `"Ca", "2"`. The session `previous_substances` (rendered into templates via the context processor) gets garbage tokens like `"H"` and `"2"`. Harmless (autoescaped), but the correct extraction already exists in `forms.py:172` (`parse_species_from_equations` splits on `\s+\+\s+`).

**Fix:** Split on `" + "` (or reuse `parse_species_from_equations`) instead of `"+"`.

### IN-02: Test-suite gaps for the findings above

**File:** `tests/chemistry_calculators/test_security.py` and `tests/chemistry_calculators/test_webapp.py`
**Issue:** The matrix is strong on the eval paths (marker-file tests, kwargs-pin, generic-copy pins) but has no coverage for: the trailing-newline acceptance hole (WR-02), the `UndefinedUnitError` crash (CR-01 — only malformed JSON is tested at `test_webapp.py:636-657`), engine-side non-finite K (WR-04), or form/engine behavioral equivalence (WR-03). The `marker` tests at `test_webapp.py:392-418` also leave predictable `/tmp` files behind (no cleanup), which can collide across parallel CI workers.

**Fix:** Add the regression tests suggested in CR-01/WR-02/WR-04, and remove the marker files in a `finally` block.

---

_Reviewed: 2026-08-05T18:30:00Z_
_Reviewer: the agent (gsd-code-reviewer)_
_Depth: standard_
