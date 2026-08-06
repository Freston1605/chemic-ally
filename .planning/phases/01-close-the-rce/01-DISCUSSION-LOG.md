# Phase 1: Close the RCE - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-05
**Phase:** 1-Close the RCE
**Areas discussed:** Shared validation module, Equation-format enforcement, Form-layer validation, Error surfacing, Adversarial regression tests, chempy 0.9.0 verification

---

## Mode

`--auto` — discussion ran autonomously. All gray areas auto-selected and resolved from research findings (`.planning/research/SUMMARY.md`, `.planning/codebase/CONCERNS.md`) with no interactive prompts. Decisions are the recommended options per the research.

---

## Shared Validation Module (SEC-05)

| Option | Description | Selected |
|--------|-------------|----------|
| Extract `calculations/security.py` | Single module for regexes + eval globals + `_is_safe_equation`, imported by form and engine | ✓ |

**User's choice:** auto (recommended default)
**Notes:** Research pitfall #6 — duplicated `_SAFE_FORMULA_RE` in `forms.py` and `equilibria.py` will drift; consolidation is part of the phase.

## Equation-Format Enforcement (SEC-01, SEC-02)

| Option | Description | Selected |
|--------|-------------|----------|
| Exactly-two-segment `formula = formula; K` only | Reject 3+ segments (kwargs-eval path), missing `=`, empty sides, bad charsets | ✓ |
| Restricted K regex `[0-9.eE+\-*/()]+` | `10**-10.3` still parses; no identifiers/calls possible | ✓ |
| Always pass `globals_={"__builtins__": {}}` | Never `globals_=False` (nulls K, re-exposes builtins) | ✓ |

**User's choice:** auto (recommended default)
**Notes:** Research pitfall #1 — `globals_=False` is an incomplete fix, empirically verified against chempy 0.9.0 source; working-tree restricted-dict fix is strictly stronger. Do not regress.

## Form-Layer Validation (SEC-03, SEC-04)

| Option | Description | Selected |
|--------|-------------|----------|
| `float()` + `isfinite()` on K | Client K interpolated into chempy string — must be a number | ✓ |
| 5000-char caps on hidden fields | Already in working tree | ✓ |
| Both layers stay | Form float-check load-bearing; engine `_is_safe_equation` defense-in-depth | ✓ |

**User's choice:** auto (recommended default)
**Notes:** Already implemented in the working tree — validate, don't rebuild.

## Error Surfacing

| Option | Description | Selected |
|--------|-------------|----------|
| Safe generic error from engine | `{"success": False, "error": "Unsafe or malformed reaction string"}` — never interpolate attacker input | ✓ |

**User's choice:** auto (recommended default)
**Notes:** Engine raises `ValueError` → existing broad handler returns failure dict.

## Adversarial Regression Tests (SEC-06, TEST-03)

| Option | Description | Selected |
|--------|-------------|----------|
| Both eval paths + side-effect assertions | Param path + kwargs path (3-segment) payloads; assert no file/marker created | ✓ |
| Engine AND view layers | Direct calculator tests + POST to equilibria endpoint | ✓ |

**User's choice:** auto (recommended default)
**Notes:** Working tree already has test additions — verify completeness against both eval paths.

## chempy 0.9.0 Verification

| Option | Description | Selected |
|--------|-------------|----------|
| REPL check `rxn_parse_kwargs` acceptance | Verify pinned 0.9.0 accepts the kwargs; bump to 0.10.1 as fallback | ✓ |

**User's choice:** auto (recommended default)
**Notes:** Research flag — verified on chempy master, not 0.9.0; implementation-time check required.

---

## the agent's Discretion

- Exact regex patterns, shared-module function naming, and test structure (behavior locked by CONTEXT.md decisions).

## Deferred Ideas

- Score anti-forgery signing → Phase 5
- Full API endpoint test suite for `/hplc/api/*` → Phase 3
- RNG seeding + golden-value engine tests → v2 (DET-01/02)
