# Phase 1: Close the RCE - Pattern Map

**Mapped:** 2026-08-05
**Files analyzed:** 6 (2 new, 4 modified)
**Analogs found:** 6 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `apps/chemistry_calculators/calculations/security.py` (NEW) | utility (shared validation boundary) | transform (input predicate) | `apps/chemistry_calculators/calculations/equilibria.py` lines 15-42 (literal source of moved code) + `apps/chemistry_calculators/calculations/units.py` (shared-module pattern) | exact (verbatim move) |
| `apps/chemistry_calculators/calculations/equilibria.py` (MODIFIED) | service (calculator engine) | transform → request-response | itself (working-tree version = analog; import swap + error split) + `apps/chemistry_calculators/views.py` lines 360-367 (`logging.exception` + generic message pattern) | exact |
| `apps/chemistry_calculators/forms.py` (MODIFIED) | form (input validation) | request-response | itself — `EquilibriumSystemForm.clean_reactions`/`clean` (lines 176-233, 235-284) | exact |
| `templates/chemistry_calculators/calculator/equilibria.html` (MODIFIED) | component (template) | request-response | itself — reactions-errors block (lines 56-58) + result-card failure block (lines 197-200); analog for a11y attribute style: `role="status"` at line 143 | exact |
| `tests/chemistry_calculators/test_webapp.py` (MODIFIED) | test (unit + integration) | request-response | itself — `EquilibriaCalculatorTests` (275-429), `EquilibriumFormTests` (431-645), `EquilibriaViewTests` (648-731) | exact |
| `tests/chemistry_calculators/test_security.py` (NEW) | test (unit, predicate) | transform | `tests/chemistry_calculators/test_webapp.py` `EquilibriaCalculatorTests` (lines 275-429) — SimpleTestCase class + `setUp` pattern | role-match |

---

## Pattern Assignments

### `apps/chemistry_calculators/calculations/security.py` (NEW — utility, transform)

**Analog:** `apps/chemistry_calculators/calculations/equilibria.py` lines 15-42 (verbatim source of the code being moved) + `apps/chemistry_calculators/calculations/units.py` (shared-module shape)

This is a **pure move, not a rewrite** (research Pitfall 4): copy the working-tree names verbatim from `equilibria.py`, rename to public names (`is_safe_equation`, `SAFE_FORMULA_RE`, `SAFE_K_VALUE_RE`, `SAFE_EVAL_GLOBALS` — naming is planner discretion per CONTEXT D-01/A1), add the two error-copy constants.

**Module shape — copied from `apps/chemistry_calculators/calculations/units.py` lines 1-13** (module-level constants, no classes, stdlib-only imports, consumed via relative imports):
```python
import pint

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity
```

**Core content — post-gap-closure shape of `apps/chemistry_calculators/calculations/security.py`** (updated by Plan 01-03: `\Z` anchors, raw `"\n"` guard, `safe_k_value`; charsets byte-identical to the pre-gap move):
```python
# module constants (post-01-03 — anchors are \Z, never $)
SAFE_FORMULA_RE = re.compile(r"^[A-Za-z0-9+\-() .\u00b7]+\Z")
SAFE_K_VALUE_RE = re.compile(r"^[0-9.eE+\-*/()]+\Z")
SAFE_EVAL_GLOBALS = {"__builtins__": {}}

def safe_k_value(k_expr: str) -> bool:
    # charset-valid K expression that evaluates to a finite number under
    # SAFE_EVAL_GLOBALS (never the default eval context); 1e999/1/0 rejected
    if SAFE_K_VALUE_RE.match(k_expr) is None:
        return False
    try:
        return math.isfinite(eval(k_expr, SAFE_EVAL_GLOBALS))
    except Exception:
        return False

def is_safe_equation(equation: str) -> bool:
    """Return True only for a ``formula = formula; K`` reaction line."""
    # Raw-newline guard MUST precede split/strip: the predicate strips each
    # ;-segment before regex matching, so \Z anchors alone cannot reject a
    # trailing/formula-side newline (WR-02 load-bearing check).
    if "\n" in equation:
        return False
    parts = equation.split(";")
    if len(parts) != 2:
        return False
    stoich, k_expr = parts[0].strip(), parts[1].strip()
    if not k_expr or not safe_k_value(k_expr):
        return False
    if "=" not in stoich:
        return False
    left, right = stoich.split("=", 1)
    if not left.strip() or not right.strip():
        return False
    return bool(
        SAFE_FORMULA_RE.match(left.strip())
        and SAFE_FORMULA_RE.match(right.strip())
    )
```

**Additions (locked contract, D-06 / UI-SPEC lines 94-106):** the two fixed error-copy constants live in security.py so both layers emit identical copy:
```python
UNSAFE_EQUATION_MESSAGE = "Unsafe or malformed reaction string"
GENERIC_SOLVER_ERROR_MESSAGE = "The equilibrium system could not be solved."
```

**Imports pattern** — relative import within the `calculations` package, mirroring `equilibria.py:11` (`from .base import CalculationBase`) and `base.py:9` (`from .units import Q_`):
```python
import re
```

**Import consumers use:** `from .security import is_safe_equation, SAFE_EVAL_GLOBALS, UNSAFE_EQUATION_MESSAGE, GENERIC_SOLVER_ERROR_MESSAGE` (equilibria.py) and `from .calculations.security import SAFE_FORMULA_RE` (forms.py — same dotted path style as the existing lazy import `forms.py:252` `from .calculations.units import Q_`).

---

### `apps/chemistry_calculators/calculations/equilibria.py` (MODIFIED — service, transform)

**Analog:** itself (working-tree version at lines 15-42, 121-176). Error-handling style analog: `apps/chemistry_calculators/views.py` lines 360-367 (`logging.exception` + fixed generic message, no `str(e)`).

**Change 1 — imports (replace lines 3-11 block):** drop local `import re` (line 4) and the `_SAFE_*` defs (lines 15-23); import from security.py:
```python
# replace lines 4 (import re) and 15-23 (regex/globals defs) with:
from .security import (
    GENERIC_SOLVER_ERROR_MESSAGE,
    SAFE_EVAL_GLOBALS,
    UNSAFE_EQUATION_MESSAGE,
    is_safe_equation,
)
```
Keep `import logging` (line 3) — `logger.exception` stays in the error path.

**Change 2 — call-site rename (lines 124 and 139):** `_is_safe_equation(equation)` → `is_safe_equation(equation)`; `{"globals_": _SAFE_EVAL_GLOBALS}` → `{"globals_": SAFE_EVAL_GLOBALS}`. **The `rxn_parse_kwargs` call (lines 137-140) is the security invariant — keep the exact shape, never `globals_=False`, never drop the kwarg (D-03 blocker):**
```python
# lines 137-140 — KEEP AS-IS except symbol rename
eqsys = EqSystem.from_string(
    reaction_string,
    rxn_parse_kwargs={"globals_": SAFE_EVAL_GLOBALS},
)
```

**Change 3 — error-handling split (replace lines 167-176):** the current single handler leaks `str(e)` (line 171). Split per research Pattern 2 (D-06):
```python
        except ValueError:
            # Validation rejection — return the fixed constant (D-06); never
            # interpolate attacker input into the message.
            return {
                "success": False,
                "error": UNSAFE_EQUATION_MESSAGE,
                "ph": None,
                "species": {},
                "sane": False,
                "info": {},
            }
        except Exception:
            # Solver/substance failures — log detail server-side, never echo
            # exception internals to the caller (CWE-209).
            logger.exception("Equilibria calculation failed")
            return {
                "success": False,
                "error": GENERIC_SOLVER_ERROR_MESSAGE,
                "ph": None,
                "species": {},
                "sane": False,
                "info": {},
            }
```

**Validation pattern** (post-01-03): raise `UnsafeEquationError` for unsafe strings so the dedicated branch returns the constant while genuine solver ValueErrors fall through to the generic logging branch:
```python
for equation in equations:
    if not is_safe_equation(equation):
        raise UnsafeEquationError("Unsafe or malformed reaction string")
```
Note: the exception text here is superseded by `UNSAFE_EQUATION_MESSAGE` in the handler — the constant is the single source of user-facing copy.

---

### `apps/chemistry_calculators/forms.py` (MODIFIED — form, request-response)

**Analog:** itself — `EquilibriumSystemForm.clean_reactions` (lines 176-233) and `clean` (lines 235-284).

**Change 1 — import (replace lines 1-9):** delete the duplicated `_SAFE_FORMULA_RE` def (line 9) and the now-unused `import re` (line 3 — verify `re` is still used elsewhere: yes, `parse_species_from_equations` uses `re.split` at line 170, so KEEP `import re`); import the shared regex:
```python
# delete line 9 (_SAFE_FORMULA_RE = re.compile(...))
# keep import re (line 3) — still used by parse_species_from_equations line 170
from .calculations.security import SAFE_FORMULA_RE
```
Import-path style matches the existing lazy import `from .calculations.units import Q_` (line 252). Top-level placement is safe (security.py imports only stdlib `re` — no circular-import risk with forms.py).

**Change 2 — use the shared regex (lines 225, 229):** `_SAFE_FORMULA_RE.match(...)` → `SAFE_FORMULA_RE.match(...)` (two call sites, both in `clean_reactions`).

**Change 3 — generic JSON error copy (replace lines 182-183):**
```python
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")
```
→
```python
        except json.JSONDecodeError:
            raise forms.ValidationError("Reactions data could not be read.")
```

**Change 4 — generic JSON error copy (replace lines 279-280):**
```python
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self.add_error("concentrations", f"Invalid JSON: {e}")
```
→
```python
            except (json.JSONDecodeError, ValueError, TypeError):
                self.add_error(
                    "concentrations", "Concentrations data could not be read."
                )
```

**Untouched validation patterns to keep** (already in working tree, load-bearing per D-04/D-05):
- K finite-float check, lines 213-222: `float(rxn["k_value"])` in try/except + `math.isfinite(k_float)` → `forms.ValidationError(f"Reaction {i}: K value must be a number.")` / `"...must be a finite number."`
- 5000-char caps on hidden fields, lines 117-130: `max_length=5000` on `reactions` and `concentrations` CharFields
- Charset checks on reactants/products, lines 225-232
- Equation reconstruction, lines 239-249: `f"{rxn['reactants']} = {rxn['products']}; {k_expr}"` where `k_expr = f"10**-{rxn['k_value']}"` (pKa) or `str(rxn["k_value"])` (Ka)

---

### `templates/chemistry_calculators/calculator/equilibria.html` (MODIFIED — component, request-response)

**Analog:** itself. Attribute-style analog: line 143 `<div class="text-center my-3" role="status">` (existing a11y attribute on the success block).

**Change — 2-line `role="alert"` (UI-SPEC line 132, a11y error-announcement):**

Line 1 — result-card failure alert, add `role="alert"` to line 197:
```html
<!-- lines 196-200 — add role="alert" to the failure alert div -->
{% else %}
  <div class="ca-alert ca-alert-danger" role="alert">
    <strong>Calculation Failed</strong>
    <p class="mb-0 mt-1">{{ result.error }}</p>
  </div>
{% endif %}
```
The body renders `{{ result.error }}` unchanged — the body text is the server constant from security.py (never interpolated client-side).

Line 2 — reactions-errors container, lines 56-58 (the `{% for error in form.reactions.errors %}` loop rendering `.ca-input-error` divs beneath the reaction table). Add `role="alert"` to the container/loop (planner picks exact form — wrap the loop in `<div role="alert">` or add the attribute to the `.ca-input-error` divs):
```html
<!-- lines 56-58 — add role="alert" to this error container -->
{% for error in form.reactions.errors %}
  <div class="ca-input-error">{{ error }}</div>
{% endfor %}
```

**Do not touch:** the frozen success block (lines 141-215) and the `{{ form.reactions.help_text|safe }}` at line 60 (pre-existing pattern, not in phase scope).

---

### `tests/chemistry_calculators/test_webapp.py` (MODIFIED — test, request-response)

**Analog:** itself — three existing equilibria test classes.

**Imports pattern (lines 1-22)** — new tests reuse these existing imports; add `from unittest.mock import patch` and `from chemistry_calculators.calculations import security` for the SEC-02 pin test:
```python
import json
import os

from django.test import SimpleTestCase, TestCase, Client
from django.urls import reverse
from django.conf import settings
from chempy import Substance

from chemistry_calculators.calculations.base import (
    MolecularWeightCalculator,
    ReactionBalancer,
    DilutionCalculator,
)
from chemistry_calculators.calculations.equilibria import EquilibriaCalculator
from chemistry_calculators.forms import (
    ChemicalReactionForm,
    EquilibriumSystemForm,
    MolecularFormulaForm,
    SolutionForm,
)

settings.SECRET_KEY = "test"
```

**Class structure pattern** — unit tests use `SimpleTestCase` with `setUp` (lines 275-279); view tests use `TestCase` with `self.client = Client()` (lines 648-652):
```python
class EquilibriaCalculatorTests(SimpleTestCase):
    """Tests for the EquilibriaCalculator backend."""

    def setUp(self):
        self.calc = EquilibriaCalculator()
```

**Marker-file side-effect assertion pattern** (lines 388-401) — every RCE tripwire must assert NO side effect, not just `success=False`:
```python
def test_malicious_k_expr_never_evaluated(self):
    """Even direct calculator calls must not eval attacker code."""
    marker = "/tmp/rce_calc_marker"
    try:
        os.path.exists(marker) and os.remove(marker)
    except OSError:
        pass
    payload = "H2O = H+ + OH-; __import__('os').system('touch %s')" % marker
    result = self.calc.calculate(
        equations=[payload],
        concentrations={},
    )
    self.assertFalse(result["success"])
    self.assertFalse(os.path.exists(marker))
```

**View-layer tripwire pattern** (lines 705-731) — the template for the missing kwargs-path view tripwire (research Code Examples lines 374-403):
```python
def test_equilibria_view_rejects_rce_payload(self):
    """A full POST with a malicious k_value must fail validation, no eval."""
    marker = "/tmp/rce_view_marker"
    try:
        os.path.exists(marker) and os.remove(marker)
    except OSError:
        pass
    data = {
        "reactions": json.dumps([
            {
                "reactants": "H2O",
                "products": "H+ + OH-",
                "k_mode": "pKa",
                "k_value": "__import__('os').system('touch %s')" % marker,
            },
        ]),
        "concentrations": "{}",
        "solvent": "H2O",
        "solvent_concentration": 55.4,
    }
    response = self.client.post(reverse("equilibria"), data)
    self.assertEqual(response.status_code, 200)
    form = response.context.get("form")
    self.assertIsNotNone(form)
    self.assertFalse(form.is_valid())
    self.assertIn("reactions", form.errors)
    self.assertFalse(os.path.exists(marker))
```
The missing kwargs-path variant smuggles `"; x=__import__('os').system('touch <marker>')"` into **reactants** (`"H2O; x=__import__('os').system(...)"`) — the form charset check rejects it before engine enforcement.

**SEC-02 mock pin test** — from research Pattern 3 (lines 267-285); add to `EquilibriaCalculatorTests`. This is the ONLY test that isolates the globals contract (side-effect tests can't distinguish regex-gate rejection from globals rejection):
```python
from unittest.mock import patch
from chemistry_calculators.calculations import security

def test_from_string_always_passes_no_builtins_globals(self):
    calc = EquilibriaCalculator()
    with patch(
        "chemistry_calculators.calculations.equilibria.EqSystem.from_string",
        return_value=type("FakeEqSystem", (), {"substances": [], "root": lambda self, c: ([], {}, True)})(),
    ) as m:
        calc.calculate(
            equations=["H2O = H+ + OH-; 10**-14/55.4"],
            concentrations={"H2O": 55.4},
        )
    m.assert_called_once()
    kwargs = m.call_args.kwargs["rxn_parse_kwargs"]
    self.assertIs(kwargs["globals_"], security.SAFE_EVAL_GLOBALS)
```

**Error-copy assertion pattern** (D-06, new) — engine layer asserts exact constant strings:
```python
# in EquilibriaCalculatorTests — validation rejection returns the constant
result = self.calc.calculate(equations=["bad"; "10**-5"], concentrations={})
self.assertEqual(result["error"], "Unsafe or malformed reaction string")

# solver failure returns generic constant (mock EqSystem.root to raise)
self.assertEqual(result["error"], "The equilibrium system could not be solved.")
```
Form layer asserts generic JSON copy (new): `self.assertIn("Reactions data could not be read.", form.errors["reactions"][0])` — add to `EquilibriumFormTests` beside `test_invalid_concentrations_json` (line 556).

**Untouched existing tests** (extend, don't replace — CONTEXT code_context): `test_no_success_on_bad_equation` (325), `test_empty_equations` (351), `test_malicious_k_expr_never_evaluated` (388), `test_extra_semicolon_parts_rejected` (403), `test_restricted_globals_still_solves_legit_systems` (418), form RCE tests (597-636), view tripwire (705).

---

### `tests/chemistry_calculators/test_security.py` (NEW — test, transform)

**Analog:** `tests/chemistry_calculators/test_webapp.py` lines 275-429 (`EquilibriaCalculatorTests` — SimpleTestCase + setUp structure, imports block lines 1-22).

New file for direct `is_safe_equation` unit tests + the SEC-05 shared-regex drift guard (research Wave 0 gaps). File name matches pytest.ini `python_files = test_*.py`.

**Imports pattern** — subset of test_webapp.py's import block, plus the module under test:
```python
import json
import os

from django.test import SimpleTestCase

from chemistry_calculators.calculations import security
from chemistry_calculators.calculations.equilibria import EquilibriaCalculator
from chemistry_calculators.forms import EquilibriumSystemForm
```

**Class + predicate-table pattern** (valid/invalid cases from SEC-01 matrix — no K / no `=` / empty sides / 0 segments / 3+ segments / charset violations):
```python
class SecurityPredicateTests(SimpleTestCase):
    def test_is_safe_equation_accepts_valid(self):
        for eq in (
            "H2O = H+ + OH-; 10**-14/55.4",
            "CH3COOH = H+ + CH3COO-; 1.75e-5",
            "AgCl(s) = Ag+ + Cl-; 10**-9.75",
            "CaCl2 = Ca+2 + 2 Cl-; 10**-0.7",
        ):
            self.assertTrue(security.is_safe_equation(eq), eq)

    def test_is_safe_equation_rejects(self):
        for eq in (
            "",                                   # 0 segments
            "H2O = H+ + OH-",                     # no K segment
            "H2O H+ + OH-; 10**-14",              # no '='
            "= H+ + OH-; 10**-14",                # empty left side
            "H2O = ; 10**-14",                    # empty right side
            "H2O = H+ + OH-; 1e-14; x=1",         # 3 segments (kwargs path)
            "H2O = H+ + OH-; __import__('os')",   # code in K segment
            "H2O = H+ + OH-; 10**-14",            # (K regex rejects '-'? no — accepted; use real rejects)
        ):
            self.assertFalse(security.is_safe_equation(eq), eq)
```
(Note: craft the reject table from the verified rejection matrix in RESEARCH.md SEC-01 — E3–E9; the above is a skeleton, exact strings are planner discretion.)

**SEC-05 drift-guard test** — asserts both layers import the SAME compiled regex objects (identity, not equality):
```python
class SharedRegexDriftTests(SimpleTestCase):
    def test_forms_and_engine_share_same_regex(self):
        from chemistry_calculators.forms import EquilibriumSystemForm
        # SAFE_FORMULA_RE imported from security.py by both layers
        self.assertIs(
            security.SAFE_FORMULA_RE,
            EquilibriumSystemForm.clean_reactions.__globals__["SAFE_FORMULA_RE"],
        )
```

---

## Shared Patterns

### Error-copy constants (CWE-209)
**Source:** `apps/chemistry_calculators/calculations/security.py` (new — D-06 / UI-SPEC copywriting contract lines 94-106)
**Apply to:** `equilibria.py` error handler, `forms.py` JSONDecodeError handlers, all D-06 test assertions
```python
UNSAFE_EQUATION_MESSAGE = "Unsafe or malformed reaction string"
GENERIC_SOLVER_ERROR_MESSAGE = "The equilibrium system could not be solved."
# form layer (UI-SPEC line 101):
#   "Reactions data could not be read." / "Concentrations data could not be read."
```
Rule: never `str(e)` or `f"...{e}"` in any user-facing form/result path. Three working-tree `str(e)` sites to eliminate: `equilibria.py:171`, `forms.py:183`, `forms.py:280`.

### Restricted-globals eval contract (D-03 — one-way security invariant)
**Source:** `apps/chemistry_calculators/calculations/equilibria.py` lines 137-140 (working tree, verified)
**Apply to:** `equilibria.py` (keep the call shape), `test_security.py` + `test_webapp.py` SEC-02 pin test
```python
eqsys = EqSystem.from_string(
    reaction_string,
    rxn_parse_kwargs={"globals_": SAFE_EVAL_GLOBALS},  # {"__builtins__": {}}
)
```
Blockers: any diff containing `globals_=False` or a `from_string` call without `rxn_parse_kwargs`.

### Marker-file side-effect assertions (SEC-06)
**Source:** `tests/chemistry_calculators/test_webapp.py` lines 388-401, 705-731
**Apply to:** every RCE tripwire (engine + view layer, param + kwargs path)
```python
marker = "/tmp/rce_view_marker"
try:
    os.path.exists(marker) and os.remove(marker)
except OSError:
    pass
# ... exercise the payload ...
self.assertFalse(os.path.exists(marker))
```

### Test scaffold conventions
**Source:** `tests/chemistry_calculators/test_webapp.py` lines 1-22
**Apply to:** `test_security.py` and additions to `test_webapp.py`
- `settings.SECRET_KEY = "test"` at module top
- `SimpleTestCase` for unit/predicate/form tests, `TestCase` for view/integration tests
- `setUp` instantiates `EquilibriaCalculator()` or `self.client = Client()`
- Run via pytest: `pytest tests/chemistry_calculators/test_webapp.py -q` (pytest.ini already sets `DJANGO_SETTINGS_MODULE`)

### Relative imports within `calculations/` package
**Source:** `equilibria.py:11` (`from .base import CalculationBase`), `base.py:9` (`from .units import Q_`)
**Apply to:** `security.py` consumers — `equilibria.py` uses `from .security import ...`; `forms.py` uses `from .calculations.security import ...` (same dotted path as the existing lazy `forms.py:252` import)

## No Analog Found

None — every new file has an exact or role-match analog. The `test_security.py` file is a new file but follows the established `test_webapp.py` test-class conventions directly.

## Metadata

**Analog search scope:** `apps/chemistry_calculators/` (calculations/, forms.py, views.py, urls.py), `templates/chemistry_calculators/calculator/`, `tests/chemistry_calculators/`, `.planning/phases/01-close-the-rce/01-UI-SPEC.md`, `pytest.ini`, `requirements/base.txt`
**Files scanned:** 10 (equilibria.py, units.py, base.py, forms.py, views.py, urls.py, __init__.py, equilibria.html, test_webapp.py, 01-UI-SPEC.md)
**Pattern extraction date:** 2026-08-05
