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
