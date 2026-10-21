"""Unit tests for the calculation engine contracts.

These cover the ``ReactionBalancer`` contract (legacy requirement API-02,
GSD requirement R008): every user-supplied reaction yields an explicit result
dict, and a failure carries the fixed, safe error message instead of an
implicit ``None`` or raw exception text.
"""

from django.test import SimpleTestCase

from chemistry_calculators.calculations.base import (
    BALANCE_ERROR_MESSAGE,
    ReactionBalancer,
)


class ReactionBalancerContractTests(SimpleTestCase):
    """The balancer always returns a dict and never leaks exception text."""

    FAILING_REACTIONS = [
        (["notAFormula"], ["H2O"]),  # unparsable formula
        (["C", "O2"], ["CO2", "H2"]),  # element present on one side only
        (["H2O"], ["H2O"]),  # same substance on both sides
        ([], ["H2O"]),  # no reactants
        (["H2O"], []),  # no products
    ]

    def test_failure_returns_explicit_result_dict(self):
        """An unbalanced or impossible reaction yields success=False, never None."""
        calc = ReactionBalancer()
        for reactants, products in self.FAILING_REACTIONS:
            with self.subTest(reactants=reactants, products=products):
                result = calc.calculate(reactants=reactants, products=products)
                self.assertIsInstance(result, dict)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"], BALANCE_ERROR_MESSAGE)
                self.assertEqual(result["reactants_balanced"], {})
                self.assertEqual(result["products_balanced"], {})

    def test_error_message_is_fixed_and_safe(self):
        """The error copy is the shared constant, not exception text."""
        calc = ReactionBalancer()
        result = calc.calculate(reactants=["notAFormula"], products=["H2O"])
        error = result["error"]
        self.assertEqual(error, BALANCE_ERROR_MESSAGE)
        for leak in ("Traceback", "ValueError", "chempy", "substance_factory"):
            self.assertNotIn(leak, error)

    def test_success_path_reports_no_error(self):
        """A balanceable reaction still reports success with empty error copy."""
        calc = ReactionBalancer()
        result = calc.calculate(reactants=["H2", "O2"], products=["H2O"])
        self.assertTrue(result["success"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["reactants_balanced"]["H2"], 2)
        self.assertEqual(result["products_balanced"]["H2O"], 2)

    def test_balance_error_message_is_a_module_constant(self):
        """The message is a stable module-level constant, not built per call."""
        self.assertIsInstance(BALANCE_ERROR_MESSAGE, str)
        self.assertTrue(BALANCE_ERROR_MESSAGE.strip())
