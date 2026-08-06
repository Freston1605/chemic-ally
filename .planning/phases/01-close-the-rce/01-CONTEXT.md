# Phase 1: Close the RCE - Context

**Gathered:** 2026-08-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Delivers a shared, no-builtins validation boundary that gates ALL chempy `EqSystem.from_string` parsing so user-controlled reaction strings can never execute code. This phase validates and completes the working-tree RCE fix (already partially implemented in `equilibria.py` + `forms.py`), extracts the shared validation module, and locks the fix in with adversarial regression tests at both engine and view layers.

</domain>

<decisions>
## Implementation Decisions

### Shared Validation Module (SEC-05)
- **D-01:** Extract a single shared module `apps/chemistry_calculators/calculations/security.py` holding the formula regex, K-value regex, eval globals dict, and the `_is_safe_equation` predicate. Both `forms.py` and `equilibria.py` import from it — no more duplicated `_SAFE_FORMULA_RE` definitions. — **Reversibility:** costly — both layers already define their own copies today; consolidating touches form and engine call sites, but is pure refactor with no migration or contract break.

### Equation-Format Enforcement (SEC-01, SEC-02)
- **D-02:** Keep the exactly-two-segment reaction line format `formula = formula; K` as the ONLY accepted shape. Reject extra `;` segments (3+ segments is the kwargs-eval path in chempy `to_reaction`), missing `=`, empty sides, and any non-`[A-Za-z0-9+\-() .·]` character in formula sides. — **Reversibility:** reversible — a stricter format only rejects inputs that are unsafe or malformed today.
- **D-03:** K expressions are restricted to numeric literals + arithmetic operators `[0-9.eE+\-*/()]+` (so `10**-10.3` still parses and computes). Never pass `globals_=False` and never leave `globals_` unset — always pass `{"__builtins__": {}}` (verified strictly stronger than `globals_=False`, which nulls the K constant AND still exposes builtins through the kwargs-eval path). — **Reversibility:** one-way — this is the security invariant of the phase; reverting reopens the RCE. Treat any diff that reverts to `globals_=False` as a blocker.

### Form-Layer Validation (SEC-03, SEC-04)
- **D-04:** `EquilibriumSystemForm.clean_reactions` rejects any K value that is not a finite float (`float()` + `math.isfinite`), rejects non-conforming reactant/product charsets, and caps hidden `reactions`/`concentrations` fields at 5000 chars. Validation errors surface as `forms.ValidationError` with per-reaction messages (already implemented in working tree). — **Reversibility:** reversible.
- **D-05:** The form-level float check is the load-bearing defense (client K values are interpolated into the chempy string); the engine-boundary `_is_safe_equation` is defense-in-depth for non-HTTP callers. Both must stay. — **Reversibility:** one-way — dropping either layer weakens the fix.

### Error Surfacing
- **D-06:** Unsafe/malformed strings at the engine boundary raise `ValueError` → caught by the existing broad handler → returned as `{"success": False, "error": <message>}`; the message must be a safe, generic string (e.g. "Unsafe or malformed reaction string") and must never interpolate attacker input or leak internals. — **Reversibility:** reversible.

### Adversarial Regression Tests (SEC-06, TEST-03)
- **D-07:** Regression suite covers BOTH chempy eval paths — the param path (e.g. `"; __import__('os')..."` in the K segment) and the kwargs path (3-segment lines like `formula = formula; 10**-1; x=__import__('os').system(...)`) — plus non-2-segment and charset violations. Tests assert on the returned result AND assert no side effects (no file/marker created, no module executed), at both engine layer (`calculations/equilibria.py` direct) and view layer (POST to the equilibria endpoint). — **Reversibility:** reversible — tests can be added/removed freely.

### chempy 0.9.0 Verification (research flag)
- **D-08:** Implementation MUST verify pinned chempy 0.9.0 accepts `EqSystem.from_string(..., rxn_parse_kwargs={"globals_": {...}})` with a one-line REPL check before finalizing (verified on master, not on 0.9.0). If 0.9.0 rejects it, bump to chempy 0.10.1 is the fallback — flag for planner. — **Reversibility:** one-way — version bump affects parsing behavior; must be tested.

### the agent's Discretion
- Exact regex patterns and the placement of the shared module's function names (planner picks specifics, but behavior is locked by D-01…D-08).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Security Contract
- `.planning/codebase/CONCERNS.md` §"Critical Security Issue" — the RCE finding, exact files/lines, and original (insufficient) fix recommendation
- `.planning/research/SUMMARY.md` §"Critical Pitfalls" #1 — why `globals_=False` is an incomplete fix (kwargs-eval re-injects builtins); #6 — regex drift
- `.planning/REQUIREMENTS.md` — SEC-01..06, TEST-03 (this phase's requirements)

### Code Under Change
- `apps/chemistry_calculators/calculations/equilibria.py` — engine: `_is_safe_equation`, `_SAFE_EVAL_GLOBALS`, `EqSystem.from_string` call (working-tree fix)
- `apps/chemistry_calculators/forms.py` §`EquilibriumSystemForm.clean_reactions`/`clean` — form-layer validation + string reconstruction (`f"{reactants} = {products}; {k_expr}"`)
- `apps/chemistry_calculators/views.py` §`CalculateEquilibriaView` — view layer wiring
- `tests/chemistry_calculators/test_webapp.py` — existing equilibria tests + working-tree adversarial additions

### Domain Invariant
- `apps/chemistry_calculators/calculations/base.py` — `CalculationBase` contract (calculators return dicts, not raises)

No external specs — requirements fully captured in decisions above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Working-tree `_SAFE_FORMULA_RE`/`_SAFE_K_VALUE_RE`/`_SAFE_EVAL_GLOBALS`/`_is_safe_equation` in `equilibria.py` — to be moved into the shared `security.py` module, not rewritten
- Working-tree `float()` + `isfinite()` K validation and 5000-char caps already in `forms.py`
- `tests/chemistry_calculators/test_webapp.py` already carries `test_no_success_on_bad_equation`, `test_empty_equations` — extend, don't replace

### Established Patterns
- Calculators are pure `CalculationBase` subclasses returning dicts; views catch exceptions and render — validation errors must flow through this contract
- Form layer validates hidden JSON fields in `clean_reactions` and reconstructs equations in `clean`
- Template renders with `{{ result|safe }}` on user-derived LaTeX (reaction balancer) — not in this phase's equilibria path, but the constraint context matters

### Integration Points
- `apps/chemistry_calculators/calculations/security.py` (new) imported by both `forms.py` and `calculations/equilibria.py`
- `tests/chemistry_calculators/test_webapp.py` — engine-layer tests import `EquilibriaCalculator` directly; view-layer tests POST the equilibria URL

</code_context>

<specifics>
## Specific Ideas

No specific user requirements — the working-tree RCE fix and research findings define the behavior. Open to standard approaches for module naming and test structure.

</specifics>

<deferred>
## Deferred Ideas

- **Score anti-forgery** (signing, HARD-07) — Phase 5; signed-token design depends on removing the committed `SECRET_KEY` fallback (Phase 5 scope)
- **API endpoint test coverage for the six `/hplc/api/*` URLs** — Phase 3; the RCE payload tripwires at view layer (TEST-03) land here in Phase 1, but the general API suite is Phase 3
- **RNG seeding + golden-value engine tests** — v2 (DET-01/02)

</deferred>

---

*Phase: 1-Close the RCE*
*Context gathered: 2026-08-05*
