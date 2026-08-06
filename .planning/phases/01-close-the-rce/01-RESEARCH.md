# Phase 1: Close the RCE - Research

**Researched:** 2026-08-05
**Domain:** chempy eval-based parser hardening (RCE elimination), Django form/engine validation boundary
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Extract a single shared module `apps/chemistry_calculators/calculations/security.py` holding the formula regex, K-value regex, eval globals dict, and the `_is_safe_equation` predicate. Both `forms.py` and `equilibria.py` import from it — no more duplicated `_SAFE_FORMULA_RE` definitions. — **Reversibility:** costly — both layers already define their own copies today; consolidating touches form and engine call sites, but is pure refactor with no migration or contract break.
- **D-02:** Keep the exactly-two-segment reaction line format `formula = formula; K` as the ONLY accepted shape. Reject extra `;` segments (3+ segments is the kwargs-eval path in chempy `to_reaction`), missing `=`, empty sides, and any non-`[A-Za-z0-9+\-() .·]` character in formula sides. — **Reversibility:** reversible.
- **D-03:** K expressions are restricted to numeric literals + arithmetic operators `[0-9.eE+\-*/()]+` (so `10**-10.3` still parses and computes). Never pass `globals_=False` and never leave `globals_` unset — always pass `{"__builtins__": {}}` (verified strictly stronger than `globals_=False`, which nulls the K constant AND still exposes builtins through the kwargs-eval path). — **Reversibility:** one-way — this is the security invariant of the phase; reverting reopens the RCE. Treat any diff that reverts to `globals_=False` as a blocker.
- **D-04:** `EquilibriumSystemForm.clean_reactions` rejects any K value that is not a finite float (`float()` + `math.isfinite`), rejects non-conforming reactant/product charsets, and caps hidden `reactions`/`concentrations` fields at 5000 chars. Validation errors surface as `forms.ValidationError` with per-reaction messages (already implemented in working tree). — **Reversibility:** reversible.
- **D-05:** The form-level float check is the load-bearing defense (client K values are interpolated into the chempy string); the engine-boundary `_is_safe_equation` is defense-in-depth for non-HTTP callers. Both must stay. — **Reversibility:** one-way — dropping either layer weakens the fix.
- **D-06:** Unsafe/malformed strings at the engine boundary raise `ValueError` → caught by the existing broad handler → returned as `{"success": False, "error": <message>}`; the message must be a safe, generic string (e.g. "Unsafe or malformed reaction string") and must never interpolate attacker input or leak internals. — **Reversibility:** reversible.
- **D-07:** Regression suite covers BOTH chempy eval paths — the param path (e.g. `"; __import__('os')..."` in the K segment) and the kwargs path (3-segment lines like `formula = formula; 10**-1; x=__import__('os').system(...)`) — plus non-2-segment and charset violations. Tests assert on the returned result AND assert no side effects (no file/marker created, no module executed), at both engine layer (`calculations/equilibria.py` direct) and view layer (POST to the equilibria endpoint). — **Reversibility:** reversible.
- **D-08:** Implementation MUST verify pinned chempy 0.9.0 accepts `EqSystem.from_string(..., rxn_parse_kwargs={"globals_": {...}})` with a one-line REPL check before finalizing (verified on master, not on 0.9.0). If 0.9.0 rejects it, bump to chempy 0.10.1 is the fallback — flag for planner. — **Reversibility:** one-way — version bump affects parsing behavior; must be tested.

### the agent's Discretion
- Exact regex patterns and the placement of the shared module's function names (planner picks specifics, but behavior is locked by D-01…D-08).

### Deferred Ideas (OUT OF SCOPE)
- **Score anti-forgery** (signing, HARD-07) — Phase 5
- **API endpoint test coverage for the six `/hplc/api/*` URLs** — Phase 3
- **RNG seeding + golden-value engine tests** — v2 (DET-01/02)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SEC-01 | Reject any reaction string that is not exactly `formula = formula; K` | chempy 0.9.0 `to_reaction` source — the `;` split + both eval paths ([VERIFIED: .venv/lib64/python3.14/site-packages/chempy/util/parsing.py:488-504]); `_is_safe_equation` predicate + full rejection matrix (E3–E9) below |
| SEC-02 | chempy parsing runs with eval locked to no-builtins globals (`{"__builtins__": {}}`), never `globals_=False` or unset | **D-08 resolved:** 0.9.0 `EqSystem.from_string` accepts `rxn_parse_kwargs` ([VERIFIED: chempy/equilibria.py from_string source + REPL probe]); restricted globals blocks BOTH payload shapes with NameError, no side effects ([VERIFIED: empirical probe]) |
| SEC-03 | K values validated as finite numbers server-side before string interpolation | `float()` + `math.isfinite` in `clean_reactions` ([VERIFIED: forms.py:213-222]); `nan`/`inf`/`1e999` rejected |
| SEC-04 | Hidden `reactions`/`concentrations` fields have length caps | `max_length=5000` already on both CharFields ([VERIFIED: forms.py:119, forms.py:126]); oversized-JSON test exists |
| SEC-05 | Shared validation module (`calculations/security.py`) holds formula/K regexes used by both layers (no drift) | Module contents + import points specified below; current duplication confirmed ([VERIFIED: equilibria.py:18-23 vs forms.py:9]); drift-guard test pattern provided |
| SEC-06 | Regression tests assert no side effects (no file/marker) for payloads targeting BOTH chempy eval paths | Full payload matrix (E1–E10/F1–F8/V1–V3) with marker assertions; working-tree coverage inventoried + gaps listed |
| TEST-03 | RCE payload tripwires asserted at both engine AND view layers | Engine-layer tripwires exist in working tree; **view-layer kwargs-path tripwire is a gap** — pattern provided |
</phase_requirements>

## Summary

The working tree already contains a correct, empirically-verified RCE fix: `_is_safe_equation` enforces exactly-two-segment `formula = formula; K` lines at the engine boundary, `EqSystem.from_string` is called with `rxn_parse_kwargs={"globals_": _SAFE_EVAL_GLOBALS}`, and `EquilibriumSystemForm.clean_reactions` validates K values as finite floats with charset checks and 5000-char caps. This research **resolves the open research flag (D-08)**: chempy 0.9.0's `EqSystem.from_string(s, substances=None, rxn_parse_kwargs=None, ...)` accepts `rxn_parse_kwargs` and forwards it to `Reaction.from_string(r, substance_keys, **rxn_parse_kwargs)` where `globals_` is a direct parameter — the fix works on the pinned version with **no version bump needed**. A REPL probe against the project venv confirmed the payloads are real (unpatched calls create a marker file via `__import__('os').system(...)` on BOTH the param and kwargs eval paths) and that restricted globals block both with `NameError` while legitimate `10**-10.3` / `10**-14/55.4` systems still parse and solve.

The phase's remaining work is refactor + contract completion, not reimplementation: (1) extract the shared `calculations/security.py` module (SEC-05) so form and engine import one copy of the regexes/globals/predicate; (2) close three **error-copy gaps** between the working tree and the locked UI-SPEC/D-06 contract — `equilibria.py:171` still returns `str(e)` for all exceptions (leaks internals on solver failures), and `forms.py:183/280` still echo `str(e)` from `JSONDecodeError` (attacker-controlled parse position); (3) add the 2-line `role="alert"` a11y hardening to the template; (4) extend the test suite with the missing view-layer kwargs tripwire, direct predicate unit tests, a shared-module drift guard, and a SEC-02 pin test (side-effect tests alone cannot distinguish "blocked by regex" from "blocked by globals").

**Primary recommendation:** Keep the working-tree fix as-is (do NOT regress to `globals_=False` — verified strictly weaker); execute the security.py extraction as a pure move of the existing names, not a rewrite; land the three error-copy changes and the a11y lines as small explicit tasks; and require the SEC-02 pin test (mock `EqSystem.from_string`, assert `rxn_parse_kwargs["globals_"] is SAFE_EVAL_GLOBALS`) so the globals contract cannot silently regress behind the regex gate.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Reaction-string format enforcement | Engine boundary (calculations) | Form (HTTP boundary) | `_is_safe_equation` is the last line before chempy and protects non-HTTP callers; form charset check is the load-bearing gate for HTTP input (D-05) |
| K-value numeric validation | Form (HTTP boundary) | Engine regex | `float()`+`isfinite` runs before string interpolation (D-04); the engine K regex only prevents *code*, not non-finite values |
| Eval globals restriction | Engine boundary | — | `EqSystem.from_string` call site is the only place chempy eval can run; form cannot influence it once the string is built |
| Error copy (no internals leak) | Engine boundary + Form | View/Template | Fixed constants in security.py; view/template only render `result.error` verbatim |
| Length caps on hidden fields | Form (HTTP boundary) | — | `max_length=5000` on CharFields rejects oversized payloads before any parsing |
| Adversarial regression tests | Test layer | — | Engine-layer (direct calculator calls) + view-layer (POST) tripwires per TEST-03 |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| chempy | 0.9.0 (pinned, in venv) | Chemical equilibria solver (`EqSystem`) | Already pinned in `requirements/base.txt:1`; `from_string` + `rxn_parse_kwargs` verified working on this exact version — **no bump needed (D-08 RESOLVED)** |
| Django | 5.2.3 (pinned) | Forms, views, templates | Already the framework; `forms.Form` validation chain is the HTTP boundary |
| pytest + pytest-django | 8.4.0 / 4.11.1 | Test framework | Already configured (`pytest.ini`, `DJANGO_SETTINGS_MODULE=config.settings.development`) |
| flake8 | 7.0.0 | Lint gate | CI runs `flake8 .`; working-tree files already pass |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `unittest.mock` (stdlib) | — | SEC-02 pin test — patch `EqSystem.from_string` and assert the globals kwarg | The one test that pins the globals contract itself |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `rxn_parse_kwargs={"globals_": {"__builtins__": {}}}` | `globals_=False` | **Rejected (locked D-03):** `False` nulls the K constant AND the kwargs path still evals `dict(parts[2:])` against auto-injected builtins — verified weaker. Treat any diff reverting to it as a blocker |
| Keep duplicated `_SAFE_FORMULA_RE` in forms.py + equilibria.py | Shared `calculations/security.py` | Duplication is the documented drift hazard (Pitfall 6 in research SUMMARY) — SEC-05 mandates extraction |
| chempy 0.10.1 | chempy 0.9.0 | Fallback only if 0.9.0 rejected `rxn_parse_kwargs` — **it doesn't**; 0.10.1 would be an unneeded parsing-behavior change mid-hardening |

**Installation:**
```bash
# No new packages. The venv already has every dependency this phase needs:
# chempy 0.9.0, pytest 8.4.0, pytest-django 4.11.1, flake8 7.0.0 (verified in .venv)
```

**Version verification:** chempy 0.9.0 confirmed installed (`import chempy; chempy.__version__` → `0.9.0`). All other stack versions confirmed via `.venv` pip list this session.

## Package Legitimacy Audit

> No new packages are installed by this phase — it hardens code around an already-pinned dependency. Gate run for completeness on the phase's central dependency:

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| chempy | PyPI | 10+ yrs (0.9.0 release; 0.10.x published 2025-09-22) | unknown (registry doesn't report) | github.com/bjodah/chempy (canonical) | [SUS] — only because download stats are unavailable; no postinstall, not deprecated, canonical repo | Pre-existing pin — no checkpoint needed (not a new install) |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** chempy — flagged solely for "unknown-downloads" (registry metadata gap, not a security signal). It is the long-standing scientific library already pinned in `requirements/base.txt:1` and present in the venv; this phase adds nothing to `requirements/`. No `checkpoint:human-verify` required for a non-install.
**New installs this phase:** none — `requirements/*.txt` must remain unchanged.

## Architecture Patterns

### System Architecture Diagram

```
Attacker / User POST  →  /equilibria  (CalculateEquilibriaView, FormView)
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ HTTP boundary: EquilibriumSystemForm                        │
│  • clean_reactions: JSON parse → per-reaction checks        │
│      - k_mode ∈ {pKa, Ka}  (forms.py:194)                   │
│      - float(k_value) + math.isfinite  (forms.py:213-222)   │
│      - SAFE_FORMULA_RE on reactants/products  (forms.py:225-232)│
│      - 5000-char caps on reactions/concentrations (CharField max_length)│
│      - errors → ValidationError("Reaction {n}: …")          │
│  • clean: reconstruct f"{reactants} = {products}; {k_expr}" │
│           k_expr = "10**-{k_value}" (pKa) | str(k_value) (Ka)│
└──────────────────────┬──────────────────────────────────────┘
                       ▼  cleaned_data["equations"] (list of str)
┌─────────────────────────────────────────────────────────────┐
│ Engine boundary: EquilibriaCalculator.calculate             │
│  • for each equation: is_safe_equation(eq)  ← from security.py│
│      - exactly 2 ';' segments  (blocks kwargs-eval path)    │
│      - K segment matches SAFE_K_VALUE_RE [0-9.eE+\-*/()]+   │
│        (blocks param-eval path code; allows 10**-10.3)      │
│      - '=' present, both sides non-empty, SAFE_FORMULA_RE   │
│  • EqSystem.from_string(rxn_parse_kwargs=                   │
│        {"globals_": SAFE_EVAL_GLOBALS})  ← {"__builtins__": {}}│
│        → chempy to_reaction eval: param path (parsing.py:504)│
│          and kwargs path (parsing.py:491) BOTH see no builtins│
│  • eqsys.root(init_conc) → species dict + pH                 │
└──────────────────────┬──────────────────────────────────────┘
                       ▼  result dict {success, ph, species, sane, info, error?}
┌─────────────────────────────────────────────────────────────┐
│ View/Template: process_calculation → result context          │
│  • success=True  → frozen success block (equilibria.html:141-215)│
│  • success=False → "Calculation Failed" + {{ result.error }} │
│      - error = UNSAFE_EQUATION_MESSAGE (validation reject)   │
│      - error = GENERIC_SOLVER_ERROR (other exceptions)       │
└─────────────────────────────────────────────────────────────┘
```

The two chempy eval paths this phase neutralizes (both inside `to_reaction`, chempy 0.9.0):
- **param path** — `parsing.py:504`: `eval(param, globals_)` where `param = parts[1]` (the K segment). Any 2+ segment line reaches it. Blocked by (a) SAFE_K_VALUE_RE (numeric-only) and (b) restricted globals (NameError on `__import__`).
- **kwargs path** — `parsing.py:491`: `if len(parts) > 2: kwargs.update(eval("dict(" + ";".join(parts[2:]) + "\n)", globals_ or {}))`. Only reachable on 3+ segment lines. Blocked by (a) exactly-2-segment enforcement in `is_safe_equation`, and (b) restricted globals — empirically, even `dict` itself is unavailable (`NameError: name 'dict' is not defined`), so this eval is inert even if a 3-segment line ever slipped through.

### Recommended Project Structure

```
apps/chemistry_calculators/
├── calculations/
│   ├── security.py            # NEW — shared validation boundary (SEC-05)
│   │                          #   SAFE_FORMULA_RE, SAFE_K_VALUE_RE,
│   │                          #   SAFE_EVAL_GLOBALS, UNSAFE_EQUATION_MESSAGE,
│   │                          #   GENERIC_SOLVER_ERROR_MESSAGE, is_safe_equation()
│   ├── equilibria.py          # MODIFIED — import from security.py; delete local
│   │                          #   regex defs + _SAFE_EVAL_GLOBALS; fix except handler
│   └── base.py                # unchanged — CalculationBase contract
├── forms.py                   # MODIFIED — import SAFE_FORMULA_RE from security.py;
│                              #   delete local dup (forms.py:9); generic JSON error copy
└── views.py                   # unchanged — CalculateEquilibriaView wires form→engine
templates/chemistry_calculators/calculator/
└── equilibria.html            # MODIFIED — 2-line role="alert" a11y only; success block frozen
tests/chemistry_calculators/
├── test_webapp.py             # MODIFIED — extend existing equilibria suites (don't replace)
└── test_security.py           # OPTIONAL NEW — direct is_safe_equation unit tests (planner discretion)
```

### Pattern 1: Shared no-builtins validation boundary (security.py)

**What:** One module owns every string-acceptance rule that feeds chempy's eval-based parser. Form and engine import the same compiled regexes, the same globals dict, and the same message constants, so the two layers cannot drift apart.
**When to use:** Any eval-based third-party parser with multiple call sites (this is the HARD-01/Pitfall-6 pattern from research SUMMARY).

```python
# apps/chemistry_calculators/calculations/security.py  (NEW)
"""Shared security boundary for chempy-based calculators.

Everything in this module is the single source of truth for what a reaction
string may contain before it reaches chempy's eval-based parser. Both the
form layer (forms.py) and the engine layer (calculations/equilibria.py)
import from here so the acceptance rules cannot drift apart (SEC-05).
"""

import re

# Chemical-formula side of a reaction line: element symbols, digits, charges
# (+/-), phase suffixes/parentheses, hydrates, and whitespace. Semicolons and
# any Python syntax are rejected so chempy never sees attacker-controlled code.
SAFE_FORMULA_RE = re.compile(r"^[A-Za-z0-9+\-() .\u00b7]+$")
# Equilibrium-constant expression: numeric literals and arithmetic operators
# only, so even chempy's internal eval cannot reference names or call anything.
SAFE_K_VALUE_RE = re.compile(r"^[0-9.eE+\-*/()]+$")
# Globals for chempy's internal eval: no builtins, no imports, no calls.
# Strictly stronger than globals_=False (which nulls K AND still exposes
# builtins through the kwargs-eval path). Never change this value.
SAFE_EVAL_GLOBALS = {"__builtins__": {}}
# Fixed, attacker-safe error copy (D-06 / UI-SPEC copywriting contract).
UNSAFE_EQUATION_MESSAGE = "Unsafe or malformed reaction string"
GENERIC_SOLVER_ERROR_MESSAGE = "The equilibrium system could not be solved."


def is_safe_equation(equation: str) -> bool:
    """Return True only for a ``formula = formula; K`` reaction line."""
    parts = equation.split(";")
    if len(parts) != 2:
        return False
    stoich, k_expr = parts[0].strip(), parts[1].strip()
    if not k_expr or not SAFE_K_VALUE_RE.match(k_expr):
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

*Source: verbatim move of the working-tree implementation ([VERIFIED: equilibria.py:18-42]); names made public — naming is planner discretion per CONTEXT.*

### Pattern 2: Differentiated engine error handling (fixes the `str(e)` leak)

**What:** The catch-all in `equilibria.py:167-176` currently returns `"error": str(e)` for every exception. That is correct for the `ValueError("Unsafe or malformed reaction string")` path (it already carries the constant) but leaks chempy/solver internals on any other failure. Split the handler so the validation rejection returns the shared constant and everything else returns the generic solver message.

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
            # Solver/substance failures — log the detail server-side but never
            # echo exception internals to the caller (CWE-209 / UI-SPEC copy
            # contract: "The equilibrium system could not be solved.").
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

*Note: the current single `except Exception as e: ... "error": str(e)` at equilibria.py:167-176 must be replaced with the two branches above — this is a required change, not optional polish (locked by D-06 + UI-SPEC copywriting contract).*

### Pattern 3: SEC-02 pin test (mock-based globals contract)

**What:** Side-effect tests (marker files) cannot distinguish "payload blocked by the regex gate" from "payload blocked by restricted globals" — if an executor drops the `rxn_parse_kwargs` but keeps `is_safe_equation`, all marker tests still pass. This test pins the call-site contract directly.

```python
from unittest.mock import patch
from chemistry_calculators.calculations import security
from chemistry_calculators.calculations.equilibria import EquilibriaCalculator

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

### Anti-Patterns to Avoid

- **Returning `str(e)` in any user-facing error:** `equilibria.py:171` and `forms.py:183/280` still do this in the working tree. `JSONDecodeError` messages include the attacker-controlled parse position; chempy exceptions leak library internals. All three must be replaced with fixed constants (CWE-209).
- **Testing only "success=False" without side-effect assertions:** a payload that is rejected by the regex gate but would execute under a weakened gate yields a passing test that proves nothing. Every RCE tripwire must assert `os.path.exists(marker) == False`.
- **Refactoring the regexes while extracting security.py:** the working-tree regexes are verified working (118 tests green). Move them verbatim; any "improvement" is scope creep with a regression risk on `AgCl(s)`, `CO3-2`, `H+`, `·` hydrates.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Sandboxing chempy's eval | A custom AST-walker/sandbox | `rxn_parse_kwargs={"globals_": {"__builtins__": {}}}` + format gates | chempy already supports `globals_`; REQUIREMENTS.md explicitly out-scopes custom eval sandboxes ("Restricted-globals + validation gates are verified sufficient"). An AST sandbox would be a new attack surface |
| Regex drift between form and engine | Copying the regex into each layer | Shared `calculations/security.py` imported by both | Documented failure mode (research SUMMARY Pitfall 6); the drift-guard identity test catches divergence |
| Rate limiting / auth for the equilibria endpoint | Anything here | Deferred to Phases 3/6 (WAF + throttles) | Out of Phase 1 scope per CONTEXT Deferred Ideas |

**Key insight:** chempy's `globals_` parameter *is* the sandbox hook — the fix is configuration of a supported parameter, not a custom containment mechanism. The remaining risk surface is only the correctness of the format gates, which is why they must be shared (SEC-05) and pinned by direct unit tests.

## Common Pitfalls

### Pitfall 1: `globals_=False` regression
**What goes wrong:** The RCE reopens while tests stay green.
**Why it happens:** `globals_=False` nulls the K constant AND the kwargs path still runs `eval("dict(" + ... + ")", globals_ or {})` against an empty dict that Python auto-populates with builtins — so `__import__('os')` still executes (empirically verified: marker file created under `globals_=False` in prior research; the 3-segment payload executed and then raised TypeError).
**How to avoid:** The SEC-02 pin test (Pattern 3) asserts the exact `SAFE_EVAL_GLOBALS` object is passed at the call site. CI flake8 + a code-review checkpoint on any diff touching `equilibria.py`'s `from_string` call.
**Warning signs:** Any occurrence of `globals_=False` or a `from_string` call without `rxn_parse_kwargs` in a diff — treat as a blocker per D-03.

### Pitfall 2: Error messages echoing attacker input or exception text
**What goes wrong:** Information disclosure — JSON parse-position strings and chempy internals returned to anonymous callers (CWE-209).
**Why it happens:** The working tree's `str(e)` in three places predates the UI-SPEC copy contract; the UI-SPEC explicitly replaced them with fixed constants but the code was not updated.
**How to avoid:** All three call sites use the constants in security.py; add form/engine tests asserting the exact message strings.
**Warning signs:** `f"...{e}"` or `str(e)` anywhere in user-facing form/result paths.

### Pitfall 3: Tests that prove nothing (regex gate masks globals regression)
**What goes wrong:** The side-effect tests pass even when the globals restriction is removed, because `is_safe_equation` rejects the payloads first.
**Why it happens:** Marker tests exercise the full defense chain; a failure of any single link makes the test fail, but the reverse is not true — a passing test does not prove *which* link blocked the payload.
**How to avoid:** The mock-based SEC-02 pin test (Pattern 3) is the only test that isolates the globals contract. Keep both the end-to-end marker tests AND the pin test.
**Warning signs:** A plan that contains marker tests but no direct globals-contract assertion.

### Pitfall 4: Regex refactor drift during security.py extraction
**What goes wrong:** Legitimate inputs start failing (e.g. `10**-14/55.4` rejected because `/` dropped from the K charset; `AgCl(s)` rejected because `()` dropped from the formula charset).
**Why it happens:** "Cleaning up" the regexes while moving them changes acceptance behavior.
**How to avoid:** Move the working-tree regexes verbatim; the valid-system tests (`test_carbonate_example`, `test_water_autoionization`, `test_simple_acid`, `test_ph_none_without_hplus`) guard the behavior.
**Warning signs:** Any diff that modifies the regex pattern strings during the extraction.

## Code Examples

Verified patterns from official sources:

### The exact chempy 0.9.0 call chain (D-08 RESOLVED)
```python
# chempy 0.9.0 — VERIFIED via source + REPL this session
# EqSystem.from_string(s, substances=None, rxn_parse_kwargs=None, comment_tokens=("#",), **kwargs)
#   → cls._BaseReaction.from_string(r, substance_keys, **(rxn_parse_kwargs or {}))
#     → Reaction.from_string(cls, string, substance_keys=None, globals_=None, **kwargs)
#       → to_reaction(line, substance_keys, token, Cls, globals_, **kwargs)
#         → eval(param, globals_)                          # param path (K segment)
#         → eval("dict(" + ";".join(parts[2:]) + "\n)", globals_ or {})  # kwargs path (3+ segments)

from chempy.equilibria import EqSystem

# Works on 0.9.0 — REPL probe output: "D-08 ACCEPTED: 1 reaction(s); param = 1.8050541516245487e-16"
eqsys = EqSystem.from_string(
    "H2O = H+ + OH-; 10**-14/55.4",
    rxn_parse_kwargs={"globals_": {"__builtins__": {}}},
)
```
*Source: chempy 0.9.0 source ([VERIFIED: chempy/equilibria.py from_string, chempy/chemistry.py:494, chempy/util/parsing.py:459-528]) + REPL probe; pattern corroborated by chempy examples ([CITED: github.com/bjodah/chempy examples — `ReactionSystem.from_string(str_rs2, rxn_parse_kwargs=dict(globals_=globals_))`]).*

### Empirical proof the payloads are real and the fix blocks them
```python
# Unpatched param path (default globals → get_parsing_context): marker CREATED
#   EqSystem.from_string("H2O = H+ + OH-; __import__('os').system('touch /tmp/m')")
# Unpatched kwargs path (3 segments): marker CREATED, then TypeError
#   EqSystem.from_string("H2O = H+ + OH-; 1e-14; x=__import__('os').system('touch /tmp/m')")
# Patched (globals_={"__builtins__": {}}): both raise NameError, NO marker
#   param path: NameError: name '__import__' is not defined
#   kwargs path: NameError: name 'dict' is not defined   ← even dict() itself is gone
```
*Verified this session against `.venv` chempy 0.9.0 ([VERIFIED: REPL probe]).*

### Working-tree call site (keep as-is)
```python
# apps/chemistry_calculators/calculations/equilibria.py:137-140 (VERIFIED)
eqsys = EqSystem.from_string(
    reaction_string,
    rxn_parse_kwargs={"globals_": _SAFE_EVAL_GLOBALS},
)
```

### View-layer kwargs-path tripwire (test gap — pattern to add)
```python
def test_equilibria_view_rejects_kwargs_path_payload(self):
    """A ';' smuggled into reactants must be rejected at the view layer too."""
    marker = "/tmp/rce_view_marker_kwargs"
    try:
        os.path.exists(marker) and os.remove(marker)
    except OSError:
        pass
    data = {
        "reactions": json.dumps([
            {
                "reactants": "H2O; x=__import__('os').system('touch %s')" % marker,
                "products": "H+ + OH-",
                "k_mode": "pKa",
                "k_value": "14.0",
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

## Runtime State Inventory

> Not a rename/refactor/migration phase in the runtime-state sense — the working tree carries uncommitted code changes (not deployed state). Category answers for completeness:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — no DB schema or stored data changes this phase | none |
| Live service config | None — the running Lambda keeps the current (vulnerable) deployed image until a future deploy; this phase only changes source | none (deploy is out of phase scope; pipeline deploys on `main` push) |
| OS-registered state | None | none |
| Secrets/env vars | None touched — `SECRET_KEY` fallback removal is Phase 5 (deferred) | none |
| Build artifacts | None — Python source changes only; `.venv` chempy 0.9.0 unchanged | none |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.4.0 + pytest-django 4.11.1 ([VERIFIED: .venv pip list]) |
| Config file | `pytest.ini` — `DJANGO_SETTINGS_MODULE = config.settings.development`, `pythonpath = .` |
| Quick run command | `.venv/bin/python -m pytest tests/chemistry_calculators/test_webapp.py -q` |
| Full suite command | `.venv/bin/python -m pytest -q` (118 pass baseline this session) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SEC-01 | 3-segment line rejected (kwargs path) | unit (engine) | `pytest tests/chemistry_calculators/test_webapp.py::EquilibriaCalculatorTests::test_extra_semicolon_parts_rejected -x` | ✅ working tree |
| SEC-01 | Exactly-2-segment predicate edge matrix (no K / no `=` / empty sides / 0 segments) | unit (predicate) | `test_security.py::test_is_safe_equation_*` | ❌ Wave 0 (new) |
| SEC-02 | Restricted globals blocks param + kwargs payloads, no marker | unit (engine) | `test_webapp.py::EquilibriaCalculatorTests::test_malicious_k_expr_never_evaluated` / `test_extra_semicolon_parts_rejected` | ✅ working tree |
| SEC-02 | Call site passes `SAFE_EVAL_GLOBALS` (mock pin) | unit (engine, mock) | `test_webapp.py::...::test_from_string_always_passes_no_builtins_globals` | ❌ Wave 0 (new — Pattern 3) |
| SEC-03 | Non-numeric K rejected | unit (form) | `test_webapp.py::EquilibriumFormTests::test_k_value_non_numeric_rejected` | ✅ working tree |
| SEC-03 | Non-finite K rejected (nan/inf/1e999) | unit (form) | `test_webapp.py::EquilibriumFormTests::test_k_value_non_finite_rejected` | ✅ working tree |
| SEC-04 | Oversized `reactions` JSON rejected | unit (form) | `test_webapp.py::EquilibriumFormTests::test_oversized_reactions_rejected` | ✅ working tree |
| SEC-05 | Shared module exports; both layers import SAME compiled regex | unit (identity) | `test_security.py::test_forms_and_engine_share_same_regex` (`assertIs` on compiled pattern objects) | ❌ Wave 0 (new) |
| SEC-05 | `is_safe_equation` valid/invalid table | unit (predicate) | `test_security.py` | ❌ Wave 0 (new) |
| SEC-06 | Param-path payload → no marker (engine) | unit (engine) | `test_malicious_k_expr_never_evaluated` | ✅ working tree |
| SEC-06 | kwargs-path payload → no marker (engine) | unit (engine) | `test_extra_semicolon_parts_rejected` | ✅ working tree |
| SEC-06/TEST-03 | Param-path payload → form invalid, no marker (view) | integration (view) | `test_equilibria_view_rejects_rce_payload` | ✅ working tree |
| SEC-06/TEST-03 | kwargs-path payload → form invalid, no marker (view) | integration (view) | `test_equilibria_view_rejects_kwargs_path_payload` (Pattern from Code Examples) | ❌ Wave 0 (new) |
| D-06 | Engine validation rejection returns exact constant | unit (engine) | `assert result["error"] == "Unsafe or malformed reaction string"` | ❌ Wave 0 (new) |
| D-06 | Solver failure returns generic constant (no `str(e)`) | unit (engine) | `assert result["error"] == "The equilibrium system could not be solved."` on a solver-failing input | ❌ Wave 0 (new) |
| D-04/UI | Form JSON-structural error uses generic copy | unit (form) | `assert "Reactions data could not be read."` in `form.errors["reactions"]` | ❌ Wave 0 (new) |
| Regression | Legit systems still solve under restricted globals | unit (engine) | `test_carbonate_example`, `test_water_autoionization`, `test_simple_acid`, `test_ph_none_without_hplus`, `test_restricted_globals_still_solves_legit_systems` | ✅ working tree |

### Sampling Rate
- **Per task commit:** `.venv/bin/python -m pytest tests/chemistry_calculators/test_webapp.py -q` (equilibria subset: `-k "Equilibria or equilibria"`)
- **Per wave merge:** `.venv/bin/python -m pytest -q` (full suite, 118-test baseline)
- **Phase gate:** Full suite green + `flake8 .` clean before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/chemistry_calculators/test_security.py` — direct `is_safe_equation` unit tests + shared-regex identity/drift test (SEC-05)
- [ ] `test_webapp.py` additions — SEC-02 mock pin test; view-layer kwargs tripwire; error-copy assertions (D-06)
- [ ] No framework install needed — pytest/pytest-django already present and configured

## Security Domain

> `security_enforcement: true` (config.json), ASVS L1, `security_block_on: "high"`. **Plans must include threat_model blocks; any high-severity finding blocks the phase.**

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — (no auth surface; session-only identity is by design, out of scope) |
| V3 Session Management | no | — (session writes in `process_calculation` unchanged; no session changes) |
| V4 Access Control | no | — (endpoint is intentionally public) |
| V5 Input Validation | **yes** | `calculations/security.py` shared regexes + `is_safe_equation` + form `float()`/`isfinite`/charset checks + 5000-char caps |
| V6 Cryptography | no | — (no crypto in this phase) |
| V7 Error Handling & Logging | **yes** | Fixed server constants (`UNSAFE_EQUATION_MESSAGE`, `GENERIC_SOLVER_ERROR_MESSAGE`, generic JSON copy); `logger.exception` keeps detail server-side; never `str(e)` to the caller (CWE-209) |

### Known Threat Patterns for {stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| chempy eval code injection (param path — K segment) | Tampering / Elevation of Privilege | `SAFE_K_VALUE_RE` numeric-only gate + `SAFE_EVAL_GLOBALS` (`{"__builtins__": {}}`) at the `from_string` call |
| chempy eval code injection (kwargs path — 3+ segment lines) | Tampering / Elevation of Privilege | Exactly-2-segment enforcement in `is_safe_equation`; restricted globals make even `dict(...)` fail (verified NameError) |
| Exception-message info disclosure (`str(e)` echoes parse position / chempy internals) | Information Disclosure | All three `str(e)` sites replaced with fixed constants; tests assert exact message strings |
| Regex ReDoS on the charset gates | Denial of Service | Regexes are single char-class scans with no nested quantifiers (low risk); input bounded by 5000-char field caps |

## Sources

### Primary (HIGH confidence)
- chempy 0.9.0 source in project venv — `EqSystem.from_string` (`chempy/equilibria.py`), `Reaction.from_string` (`chempy/chemistry.py:494-542`), `to_reaction` eval paths (`chempy/util/parsing.py:459-528`) — read this session; **D-08 flag resolved: `rxn_parse_kwargs` accepted on 0.9.0**
- REPL probes against `.venv` chempy 0.9.0 — unpatched param/kwargs paths create markers (RCE real); restricted globals raise NameError on both, no markers; legit `10**-10.3`, `10**-14/55.4`, `AgCl(s)`, `CaCl2·6H2O` parse and solve
- Working-tree code — `equilibria.py`, `forms.py`, `views.py:380-443`, `test_webapp.py` (all read this session); 118-test full-suite pass + flake8 clean
- Locked contracts — `01-CONTEXT.md` (D-01…D-08), `01-UI-SPEC.md` (copywriting contract, a11y, frozen success block), `REQUIREMENTS.md` (SEC-01…06, TEST-03), `.planning/research/SUMMARY.md` (Pitfalls 1 & 6)

### Secondary (MEDIUM confidence)
- Context7 `/bjodah/chempy` — corroborates `ReactionSystem.from_string(..., rxn_parse_kwargs=dict(globals_=...))` as the documented custom-globals pattern ([CITED: chempy examples])
- PyPI registry metadata for chempy via package-legitimacy seam — canonical repo, no postinstall, not deprecated; SUS only on unknown download stats

### Tertiary (LOW confidence)
- none — every load-bearing claim in this research was verified against source or empirically this session

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — chempy 0.9.0 API verified from installed source + REPL; no new packages
- Architecture: HIGH — all three layers (form/engine/view) read; error-copy gaps identified by direct comparison of working tree vs locked UI-SPEC
- Pitfalls: HIGH — RCE exploitability empirically demonstrated (marker files) on both eval paths; `globals_=False` weakness documented in prior research and consistent with source

**Research date:** 2026-08-05
**Valid until:** 2026-09-04 (30 days — chempy 0.9.0 is pinned; stack is stable; no fast-moving deps this phase)

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Keeping the working-tree underscore names (`_SAFE_FORMULA_RE` etc.) vs public names in security.py is pure style | Pattern 1 | Low — CONTEXT grants naming discretion; tests assert behavior, not names |
| A2 | The `test_security.py` split (new file) vs extending `test_webapp.py` is planner discretion | Project Structure | Low — CONTEXT says "extend, don't replace" the existing equilibria tests; a new file for the predicate unit tests does not replace anything |
| A3 | `role="alert"` a11y lines and the three error-copy changes are required deliverables (from locked UI-SPEC), not optional | Summary / Gaps | Low — UI-SPEC explicitly labels them; a plan omitting them fails the UI contract |
| A4 | Marker files under `/tmp` are an acceptable side-effect assertion mechanism | Payload matrix | Low — pre-existing pattern in working-tree tests; OSError-guarded cleanup already present |
| A5 | No deploy/runtime-state migration is needed this phase (Lambda keeps old image until a deploy on `main`) | Runtime State Inventory | Low — deploy pipeline is out of phase scope; the fix ships via the normal pipeline after merge |

## Open Questions

1. **Ka-mode engine solve coverage** — the working tree tests Ka-mode *string reconstruction* (form) but no full engine solve with a Ka value like `1.75e-5`.
   - What we know: `1.75e-5` matches `SAFE_K_VALUE_RE` and chempy evaluates numeric literals fine under restricted globals (verified for the `e`-notation in `10**-14`).
   - What's unclear: whether a Ka-mode system solves end-to-end (no test asserts it).
   - Recommendation: planner adds one small engine-layer test (acetic acid with `CH3COOH = H+ + CH3COO-; 1.75e-5`) — cheap, closes the coverage corner.
2. **Hydrate `·` formula solve** — `_SAFE_FORMULA_RE` permits `\u00b7` and chempy parses `CaCl2·6H2O` (verified), but no equilibria test uses a hydrate.
   - What we know: parse verified in REPL; regex intent is hydrate support.
   - What's unclear: end-to-end solve with a hydrate substance in the system.
   - Recommendation: optional; flag as nice-to-have, not a gate.
