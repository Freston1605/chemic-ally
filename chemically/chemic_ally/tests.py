from django.test import TestCase
from chemic_ally.utils.calculations import calculate_molecular_weight, balance_chemical_reaction


class TestUtils(TestCase):
    def test_calculate_molecular_weight(self):
        # Test your calculate_molecular_weight function
        result = calculate_molecular_weight("H2O")
        self.assertAlmostEqual(result, 18.01528, places=5)

    def test_balance_chemical_reaction(self):
        # Test your balance_chemical_reaction function
        reactants = {"H2": 2, "O2": 1}
        products = {"H2O": 2}
        result = balance_chemical_reaction(reactants, products)
        expected_result = ({"H2": 2, "O2": 1}, {"H2O": 2})
        self.assertEqual(result, expected_result)
