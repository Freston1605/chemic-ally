import json

from django.test import SimpleTestCase, TestCase, Client
from django.urls import reverse
from django.conf import settings
from chempy import Substance

from webapp.calculations.base import (
    MolecularWeightCalculator,
    ReactionBalancer,
    DilutionCalculator,
)
from webapp.calculations.equilibria import EquilibriaCalculator
from webapp.forms import (
    ChemicalReactionForm,
    EquilibriumSystemForm,
    MolecularFormulaForm,
    SolutionForm,
)

settings.SECRET_KEY = "test"


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
        form = ChemicalReactionForm(
            {"reactant": "H2 O2", "product": "H2O", "reversible": True}
        )
        self.assertTrue(form.is_valid())

    def test_chemical_reaction_form_missing(self):
        form = ChemicalReactionForm({"reactant": "", "product": "", "reversible": True})
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Reactant and product must be provided.",
            form.errors["__all__"][0],
        )

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
        form = SolutionForm(
            {
                "v1": "0",
                "v1_unit": "L",
                "c1": "1",
                "c1_unit": "mol/L",
                "c2": "",
                "v2": "2",
                "v2_unit": "L",
            }
        )
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
        response = self.client.post(
            reverse("molecular_weight"), {"formula": "H2O"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"], {"H2O": 18.015})

    def test_reaction_balancer_view_post(self):
        data = {
            "reactant": "H2 O2",
            "product": "H2O",
            "reversible": True,
        }
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
        self.assertEqual(
            response.context["result"]["property"], "Initial Volume"
        )

    def test_dilution_view_post_missing_c2_returns_requested_unit(self):
        data = {
            "c1": "1",
            "c1_unit": "mol/L",
            "v1": "1",
            "v1_unit": "L",
            "c2": "",
            "c2_unit": "mol/L",
            "v2": "2",
            "v2_unit": "L",
        }
        response = self.client.post(reverse("dilution"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["result"]["property"], "Final Concentration"
        )
        self.assertAlmostEqual(response.context["result"]["value"], 0.5)
        self.assertEqual(response.context["result"]["unit"], "mol/L")


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



class EquilibriaCalculatorTests(SimpleTestCase):
    """Tests for the EquilibriaCalculator backend."""

    def setUp(self):
        self.calc = EquilibriaCalculator()

    def test_carbonate_example(self):
        """Reproduce the example from the issue: carbonate/bicarbonate system."""
        equations = [
            "HCO3- = H+ + CO3-2; 10**-10.3",
            "H2CO3 = H+ + HCO3-; 10**-6.3",
            "H2O = H+ + OH-; 10**-14/55.4",
        ]
        concentrations = {"HCO3-": 1e-2}
        result = self.calc.calculate(
            equations=equations,
            concentrations=concentrations,
            solvent="H2O",
            solvent_concentration=55.4,
        )

        self.assertTrue(result["success"])
        self.assertIsNotNone(result["ph"])
        # Expected pH ~8.30 from the example
        self.assertAlmostEqual(result["ph"], 8.30, places=2)
        self.assertIn("H+", result["species"])
        self.assertIn("HCO3-", result["species"])
        self.assertIn("CO3-2", result["species"])
        self.assertIn("OH-", result["species"])
        self.assertTrue(result["species"]["H2O"] > 0)

    def test_simple_acid(self):
        """A simple monoprotic weak acid (acetic acid)."""
        equations = [
            "CH3COOH = H+ + CH3COO-; 10**-4.76",
            "H2O = H+ + OH-; 10**-14/55.4",
        ]
        concentrations = {"CH3COOH": 0.1}
        result = self.calc.calculate(
            equations=equations,
            concentrations=concentrations,
            solvent="H2O",
            solvent_concentration=55.4,
        )

        self.assertTrue(result["success"])
        self.assertIsNotNone(result["ph"])
        # Acetic acid 0.1 M: pH ~2.88
        self.assertAlmostEqual(result["ph"], 2.88, places=1)

    def test_no_success_on_bad_equation(self):
        """Malformed equation should return success=False."""
        equations = [
            "this is not valid; 10**-5",
        ]
        concentrations = {"H2O": 55.4}
        result = self.calc.calculate(
            equations=equations,
            concentrations=concentrations,
        )
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_ph_none_without_hplus(self):
        """If H+ is not a species, pH should be None."""
        equations = [
            "AgCl(s) = Ag+ + Cl-; 10**-9.75",
        ]
        concentrations = {"AgCl(s)": 1.0}
        result = self.calc.calculate(
            equations=equations,
            concentrations=concentrations,
        )
        self.assertTrue(result["success"])
        self.assertIsNone(result["ph"])

    def test_empty_equations(self):
        """Empty equations list should fail."""
        result = self.calc.calculate(
            equations=[],
            concentrations={"H2O": 55.4},
        )
        self.assertFalse(result["success"])

    def test_water_autoionization(self):
        """Pure water: pH should be 7."""
        equations = [
            "H2O = H+ + OH-; 10**-14/55.4",
        ]
        concentrations = {}
        result = self.calc.calculate(
            equations=equations,
            concentrations=concentrations,
            solvent="H2O",
            solvent_concentration=55.4,
        )
        self.assertTrue(result["success"])
        self.assertIsNotNone(result["ph"])
        self.assertAlmostEqual(result["ph"], 7.0, places=1)

    def test_sane_flag(self):
        """The sane flag should be a boolean."""
        equations = [
            "H2O = H+ + OH-; 10**-14/55.4",
        ]
        concentrations = {}
        result = self.calc.calculate(
            equations=equations,
            concentrations=concentrations,
        )
        self.assertIn("sane", result)
        self.assertIsInstance(result["sane"], bool)


class EquilibriumFormTests(SimpleTestCase):
    """Tests for the EquilibriumSystemForm."""

    def _valid_reactions_json(self):
        """Helper: return a valid reactions JSON string for the default example."""
        return json.dumps([
            {"reactants": "HCO3-", "products": "H+ + CO3-2", "k_mode": "pKa", "k_value": "10.3"},
            {"reactants": "H2CO3", "products": "H+ + HCO3-", "k_mode": "pKa", "k_value": "6.3"},
            {"reactants": "H2O", "products": "H+ + OH-", "k_mode": "pKa", "k_value": "14.0"},
        ])

    def test_valid_form(self):
        form = EquilibriumSystemForm({
            "reactions": self._valid_reactions_json(),
            "concentrations": '{"HCO3-": {"value": 0.01, "unit": "mol/L"}}',
            "solvent": "H2O",
            "solvent_concentration": 55.4,
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(len(form.cleaned_data["equations"]), 3)
        # Verify the reconstructed equations
        self.assertEqual(
            form.cleaned_data["equations"],
            [
                "HCO3- = H+ + CO3-2; 10**-10.3",
                "H2CO3 = H+ + HCO3-; 10**-6.3",
                "H2O = H+ + OH-; 10**-14.0",
            ],
        )
        # Verify concentrations are converted to mol/L
        self.assertAlmostEqual(form.cleaned_data["concentrations"]["HCO3-"], 0.01)

    def test_valid_form_concentration_unit_conversion(self):
        """Concentrations in non-default units should be converted to mol/L."""
        form = EquilibriumSystemForm({
            "reactions": self._valid_reactions_json(),
            "concentrations": '{"HCO3-": {"value": 10, "unit": "mmol/L"}}',
        })
        self.assertTrue(form.is_valid())
        # 10 mmol/L = 0.01 mol/L
        self.assertAlmostEqual(form.cleaned_data["concentrations"]["HCO3-"], 0.01)

    def test_valid_form_backward_compat_plain_number(self):
        """Plain number (no unit dict) should be treated as mol/L."""
        form = EquilibriumSystemForm({
            "reactions": self._valid_reactions_json(),
            "concentrations": '{"HCO3-": 0.01}',
        })
        self.assertTrue(form.is_valid())
        self.assertAlmostEqual(form.cleaned_data["concentrations"]["HCO3-"], 0.01)

    def test_valid_form_ka_mode(self):
        """Ka mode should use the raw value directly."""
        reactions = json.dumps([
            {"reactants": "CH3COOH", "products": "H+ + CH3COO-", "k_mode": "Ka", "k_value": "1.75e-5"},
        ])
        form = EquilibriumSystemForm({
            "reactions": reactions,
            "concentrations": '{"CH3COOH": 0.1}',
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(
            form.cleaned_data["equations"],
            ["CH3COOH = H+ + CH3COO-; 1.75e-5"],
        )

    def test_missing_reactions(self):
        form = EquilibriumSystemForm({
            "reactions": "",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)

    def test_reactions_empty_array(self):
        form = EquilibriumSystemForm({
            "reactions": "[]",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)

    def test_reactions_missing_k_mode(self):
        reactions = json.dumps([
            {"reactants": "H2O", "products": "H+ + OH-", "k_value": "14.0"},
        ])
        form = EquilibriumSystemForm({
            "reactions": reactions,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)

    def test_reactions_missing_reactants(self):
        reactions = json.dumps([
            {"reactants": "", "products": "H+ + OH-", "k_mode": "pKa", "k_value": "14.0"},
        ])
        form = EquilibriumSystemForm({
            "reactions": reactions,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)

    def test_invalid_concentrations_json(self):
        form = EquilibriumSystemForm({
            "reactions": self._valid_reactions_json(),
            "concentrations": "not-json",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("concentrations", form.errors)

    def test_parse_species_from_equations(self):
        """Static method should correctly extract species from equations."""
        equations = (
            "HCO3- = H+ + CO3-2; 10**-10.3\n"
            "H2CO3 = H+ + HCO3-; 10**-6.3\n"
            "H2O = H+ + OH-; 10**-14/55.4"
        )
        species = EquilibriumSystemForm.parse_species_from_equations(equations)
        expected = {"HCO3-", "H+", "CO3-2", "H2CO3", "H2O", "OH-"}
        self.assertEqual(species, expected)

    def test_parse_species_empty(self):
        """Empty text should produce an empty set."""
        species = EquilibriumSystemForm.parse_species_from_equations("")
        self.assertEqual(species, set())

    def test_parse_species_single_equation(self):
        """Single equation should parse correctly."""
        equations = "CH3COOH = H+ + CH3COO-; 10**-4.76"
        species = EquilibriumSystemForm.parse_species_from_equations(equations)
        expected = {"CH3COOH", "H+", "CH3COO-"}
        self.assertEqual(species, expected)


class EquilibriaViewTests(TestCase):
    """Tests for the CalculateEquilibriaView."""

    def setUp(self):
        self.client = Client()

    def test_equilibria_view_get(self):
        response = self.client.get(reverse("equilibria"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "webapp/calculator/equilibria.html")

    def test_equilibria_view_post_valid(self):
        data = {
            "reactions": json.dumps([
                {"reactants": "HCO3-", "products": "H+ + CO3-2", "k_mode": "pKa", "k_value": "10.3"},
                {"reactants": "H2CO3", "products": "H+ + HCO3-", "k_mode": "pKa", "k_value": "6.3"},
                {"reactants": "H2O", "products": "H+ + OH-", "k_mode": "pKa", "k_value": "14.0"},
            ]),
            "concentrations": '{"HCO3-": {"value": 0.01, "unit": "mol/L"}}',
            "solvent": "H2O",
            "solvent_concentration": 55.4,
        }
        response = self.client.post(reverse("equilibria"), data)
        self.assertEqual(response.status_code, 200)
        result = response.context.get("result")
        self.assertIsNotNone(result)
        self.assertTrue(result.get("success", False))
        self.assertIsNotNone(result.get("ph"))

    def test_equilibria_view_post_invalid(self):
        data = {
            "reactions": "",
        }
        response = self.client.post(reverse("equilibria"), data)
        self.assertEqual(response.status_code, 200)
        # Should re-render form with errors
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertFalse(form.is_valid())


class LoggingConfigTests(SimpleTestCase):
    def test_logging_file_handler_path(self):
        """Ensure LOGGING writes to the expected file."""
        file_handler = settings.LOGGING['handlers']['file']
        expected_path = settings.BASE_DIR / 'django.log'
        self.assertEqual(file_handler['filename'], expected_path)
