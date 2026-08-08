"""Shared security boundary for chempy-based calculators.

Everything in this module is the single source of truth for what a reaction
string may contain before it reaches chempy's eval-based parser. Both the
form layer (forms.py) and the engine layer (calculations/equilibria.py)
import from here so the acceptance rules cannot drift apart (SEC-05).
"""

import math
import re

# Chemical-formula side of a reaction line: element symbols, digits, charges
# (+/-), phase suffixes/parentheses, hydrates, and whitespace. Semicolons and
# any Python syntax are rejected so chempy never sees attacker-controlled code.
# \Z (not $) is required because Python's $ matches before a final newline —
# code matching these regexes directly must not admit a trailing newline into
# the exactly-`formula = formula; K` contract (D-02 / WR-02 defense-in-depth).
SAFE_FORMULA_RE = re.compile(r"^[A-Za-z0-9+\-() .\u00b7]+\Z")
# Equilibrium-constant expression: numeric literals and arithmetic operators
# only, so even chempy's internal eval cannot reference names or call anything.
SAFE_K_VALUE_RE = re.compile(r"^[0-9.eE+\-*/()]+\Z")
# Globals for chempy's internal eval: no builtins, no imports, no calls.
# Strictly stronger than globals_=False (which nulls K AND still exposes
# builtins through the kwargs-eval path). Never change this value.
SAFE_EVAL_GLOBALS = {"__builtins__": {}}
# Fixed, attacker-safe error copy (D-06 / UI-SPEC copywriting contract).
UNSAFE_EQUATION_MESSAGE = "Unsafe or malformed reaction string"
GENERIC_SOLVER_ERROR_MESSAGE = "The equilibrium system could not be solved."


def safe_k_value(k_expr: str) -> bool:
    """True only for a charset-valid K expression evaluating to a finite number.

    Evaluates the expression under SAFE_EVAL_GLOBALS — the same no-builtins
    globals chempy's internal eval uses, so this adds no new attack surface
    (D-03/D-05 layered defense for non-HTTP callers); non-finite or
    non-evaluable expressions (``1e999``, ``1/0``) are rejected before they
    can reach chempy. Never use the default eval context.
    """
    if SAFE_K_VALUE_RE.match(k_expr) is None:
        return False
    try:
        return math.isfinite(eval(k_expr, SAFE_EVAL_GLOBALS))  # noqa: S307
    except Exception:
        return False


def is_safe_equation(equation: str) -> bool:
    """Return True only for a ``formula = formula; K`` reaction line."""
    # Raw-newline rejection MUST precede split/strip: the predicate strips
    # each ;-segment before regex matching, so a trailing or formula-side
    # newline never reaches the regexes and anchors alone cannot reject it
    # (empirically verified: with only \Z anchors, "...10**-14\n" still
    # returns True). Legit equations never contain \n: the engine validates
    # each equation individually and only THEN joins them with "\n"
    # (equilibria.py), and the form reconstructs each equation newline-free.
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
