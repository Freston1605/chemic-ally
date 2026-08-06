"""Direct unit tests for the shared security predicate (SEC-01 acceptance matrix).

Covers the ``formula = formula; K`` acceptance rules at the predicate level:
exactly-2-segment enforcement, K-value charset, "=" presence, empty sides,
0/3+ segment shapes, and code payloads on both chempy eval paths.
"""

from django.conf import settings
from django.test import SimpleTestCase

from chemistry_calculators.calculations import security

settings.SECRET_KEY = "test"


class SecurityPredicateTests(SimpleTestCase):
    """SEC-01 acceptance matrix asserted directly against is_safe_equation."""

    def test_is_safe_equation_accepts_valid(self):
        valid = (
            "H2O = H+ + OH-; 10**-14/55.4",
            "CH3COOH = H+ + CH3COO-; 1.75e-5",
            "AgCl(s) = Ag+ + Cl-; 10**-9.75",
            "CaCl2 = Ca+2 + 2 Cl-; 10**-0.7",
            "CaCl2\u00b76H2O = Ca+2 + 2 Cl- + 6 H2O; 10**-0.7",
        )
        for eq in valid:
            self.assertTrue(security.is_safe_equation(eq), eq)

    def test_is_safe_equation_rejects(self):
        reject = (
            "",                                    # 0 segments
            "H2O = H+ + OH-",                     # no K segment
            "H2O H+ + OH-; 10**-14",              # no "="
            "= H+ + OH-; 10**-14",                # empty left side
            "H2O = ; 10**-14",                    # empty right side
            "H2O = H+ + OH-; 1e-14; x=1",         # 3 segments (kwargs path)
            "H2O = H+ + OH-; __import__('os')",   # code in K segment
            "H2O = H+ + OH-; 14; x = __import__('os').system('true')",
            "H2O = H+ + OH-; not-a-number",       # K charset violation
        )
        for eq in reject:
            self.assertFalse(security.is_safe_equation(eq), eq)
