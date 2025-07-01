from django.test import SimpleTestCase, TestCase, Client
from django.urls import reverse
from django.conf import settings
import os

settings.SECRET_KEY = "test"
os.makedirs('/var/log/django', exist_ok=True)

from ..calculations.base import (
    MolecularWeightCalculator,
    ReactionBalancer,
    DilutionCalculator,
)
from chempy import Substance
from ..forms import MolecularFormulaForm, ChemicalReactionForm, SolutionForm


class CalculatorTests(SimpleTestCase):
    def test_molecular_weight_valid(self):
        calc = MolecularWeightCalculator()
        self.assertAlmostEqual(calc.calculate("H2O"), 18.015, places=3)

    def test_molecular_weight_invalid(self):
        calc = MolecularWeightCalculator()
        self.assertIsNone(calc.calculate("XYZ"))

    def test_reaction_balancer_valid(self):
        calc = ReactionBalancer()
        result = calc.calculate(["H2", "O2"], ["H2O"])
        self.assertIsNotNone(result)
        reactants, products = result
        self.assertEqual(reactants["H2"], 2)
        self.assertEqual(products["H2O"], 2)

    def test_reaction_balancer_invalid(self):
        calc = ReactionBalancer()
        result = calc.calculate(["notAFormula"], ["H2O"])
        self.assertIsNone(result)

    def test_dilution_missing_v1(self):
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

    def test_dilution_mass_calculation(self):
        calc = DilutionCalculator()
        res = calc.calculate(
            c1=None,
            c1_unit="mol/L",
            v1=1.0,
            v1_unit="L",
            c2=0.5,
            c2_unit="mol/L",
            v2=2.0,
            v2_unit="L",
            molecular_weight=58.44,
        )
        self.assertEqual(res["missing_property"], "c1")
        self.assertIn("mass_g", res)
        self.assertAlmostEqual(res["mass_g"], 58.44, places=2)

    def test_dilution_mass_from_formula(self):
        calc = DilutionCalculator()
        res = calc.calculate(
            c1=None,
            c1_unit="mol/L",
            v1=1.0,
            v1_unit="L",
            c2=0.5,
            c2_unit="mol/L",
            v2=2.0,
            v2_unit="L",
            molecular_weight=None,
            solute_formula="NaCl",
        )
        self.assertEqual(res["missing_property"], "c1")
        self.assertIn("mass_g", res)

    def test_molecular_weight_with_substance(self):
        calc = MolecularWeightCalculator()
        h2o = Substance.from_formula("H2O")
        self.assertAlmostEqual(calc.calculate(h2o), 18.015, places=3)


class FormTests(SimpleTestCase):
    def test_molecular_formula_form_valid(self):
        form = MolecularFormulaForm({"formula": "H2O"})
        self.assertTrue(form.is_valid())

    def test_molecular_formula_form_invalid(self):
        form = MolecularFormulaForm({"formula": ""})
        self.assertFalse(form.is_valid())

    def test_chemical_reaction_form_valid(self):
        form = ChemicalReactionForm({"reactant": "H2 O2", "product": "H2O", "reversible": True})
        self.assertTrue(form.is_valid())

    def test_chemical_reaction_form_missing(self):
        form = ChemicalReactionForm({"reactant": "", "product": "", "reversible": True})
        self.assertFalse(form.is_valid())
        self.assertIn("Reactant and product must be provided.", form.errors["__all__"][0])

    def test_solution_form_valid(self):
        form = SolutionForm({
            "c1": "1",
            "c1_unit": "mol/L",
            "v1": "",
            "v1_unit": "L",
            "c2": "0.5",
            "c2_unit": "mol/L",
            "v2": "2",
            "v2_unit": "L",
        })
        self.assertTrue(form.is_valid())

    def test_solution_form_negative(self):
        form = SolutionForm({"c1": "-1", "c1_unit": "mol/L"})
        self.assertFalse(form.is_valid())

    def test_solution_form_zero(self):
        form = SolutionForm({"v1": "0", "v1_unit": "L", "c1": "1", "c1_unit": "mol/L", "c2": "", "v2": "2", "v2_unit": "L"})
        self.assertFalse(form.is_valid())

    def test_solution_form_wrong_field_count(self):
        form = SolutionForm({
            "c1": "1",
            "c1_unit": "mol/L",
            "v1": "1",
            "v1_unit": "L",
            "c2": "0.5",
            "c2_unit": "mol/L",
            "v2": "2",
            "v2_unit": "L",
        })
        self.assertFalse(form.is_valid())

    def test_solution_form_final_volume_lt_initial(self):
        form = SolutionForm({
            "c1": "1",
            "c1_unit": "mol/L",
            "v1": "5",
            "v1_unit": "L",
            "c2": "",
            "c2_unit": "mol/L",
            "v2": "2",
            "v2_unit": "L",
        })
        self.assertFalse(form.is_valid())

    def test_solution_form_final_concentration_gt_initial(self):
        form = SolutionForm({
            "c1": "1",
            "c1_unit": "mol/L",
            "v1": "1",
            "v1_unit": "L",
            "c2": "2",
            "c2_unit": "mol/L",
            "v2": "",
            "v2_unit": "L",
        })
        self.assertFalse(form.is_valid())


class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_molecular_weight_view_get(self):
        response = self.client.get(reverse("molecular_weight"))
        self.assertEqual(response.status_code, 200)

    def test_molecular_weight_view_post(self):
        response = self.client.post(reverse("molecular_weight"), {"formula": "H2O"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"], {"H2O": 18.015})

    def test_reaction_balancer_view_post(self):
        data = {"reactant": "H2 O2", "product": "H2O", "reversible": True}
        response = self.client.post(reverse("reaction_balancer"), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("\\ce", response.context["result"])

    def test_dilution_view_post(self):
        data = {
            "c1": "1",
            "c1_unit": "mol/L",
            "v1": "",
            "v1_unit": "L",
            "c2": "0.5",
            "c2_unit": "mol/L",
            "v2": "2",
            "v2_unit": "L",
        }
        response = self.client.post(reverse("dilution"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"]["property"], "Initial Volume")

class ContextProcessorTests(TestCase):
    def setUp(self):
        self.client = Client()
        session = self.client.session
        session['previous_substances'] = ['H2O']
        session.save()

    def test_context_available(self):
        response = self.client.get(reverse('molecular_weight'))
        self.assertEqual(response.context['previous_substances'], ['H2O'])

    def test_session_updated_on_calculation(self):
        self.client.post(reverse('molecular_weight'), {'formula': 'CO2'})
        session = self.client.session
        self.assertIn('CO2', session['previous_substances'])


class LoggingConfigTests(SimpleTestCase):
    def test_logging_file_handler_path(self):
        """Ensure LOGGING writes to the expected file."""
        file_handler = settings.LOGGING['handlers']['file']
        self.assertEqual(file_handler['filename'], '/var/log/django/django.log')
