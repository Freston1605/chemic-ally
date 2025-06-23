import unittest

from .calculations.base import (
    MolecularWeightCalculator,
    ReactionBalancer,
    DilutionCalculator,
)
from .utils.units import Q_


class TestCalculations(unittest.TestCase):
    def test_molecular_weight_calculator(self):
        calc = MolecularWeightCalculator()
        weight = calc.calculate("H2O")
        # Expected molar mass of water ~18.015 g/mol
        self.assertAlmostEqual(weight, 18.015, places=3)

    def test_reaction_balancer(self):
        calc = ReactionBalancer()
        result = calc.calculate(["H2", "O2"], ["H2O"])
        self.assertIsNotNone(result)
        reactants, products = result
        self.assertEqual(reactants.get("H2"), 2)
        self.assertEqual(reactants.get("O2"), 1)
        self.assertEqual(products.get("H2O"), 2)

    def test_dilution_calculator(self):
        calc = DilutionCalculator()
        result = calc.calculate(
            c1=1.0,
            c1_unit="mol/L",
            v1=None,
            v1_unit="L",
            c2=0.5,
            c2_unit="mol/L",
            v2=2.0,
            v2_unit="L",
        )
        self.assertEqual(result["missing_property"], "v1")
        self.assertAlmostEqual(result["missing_value"].to("L").magnitude, 1.0)


if __name__ == "__main__":
    unittest.main()
