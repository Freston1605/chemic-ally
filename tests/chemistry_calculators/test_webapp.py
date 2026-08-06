import json
import os
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, Client
from django.urls import reverse
from django.conf import settings
from chempy import Substance

from chemistry_calculators.calculations import security
from chemistry_calculators.calculations.base import (
    MolecularWeightCalculator,
    ReactionBalancer,
    DilutionCalculator,
)
from chemistry_calculators.calculations.equilibria import EquilibriaCalculator
from chemistry_calculators.forms import (
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

    def test_malicious_k_expr_never_evaluated(self):
        """Even direct calculator calls must not eval attacker code."""
        marker = "/tmp/rce_calc_marker"
        try:
            os.path.exists(marker) and os.remove(marker)
        except OSError:
            pass
        payload = "H2O = H+ + OH-; __import__('os').system('touch %s')" % marker
        result = self.calc.calculate(
            equations=[payload],
            concentrations={},
        )
        self.assertFalse(result["success"])
        self.assertFalse(os.path.exists(marker))

    def test_extra_semicolon_parts_rejected(self):
        """A third ';' segment must not reach chempy's dict(...) eval."""
        marker = "/tmp/rce_calc_marker2"
        try:
            os.path.exists(marker) and os.remove(marker)
        except OSError:
            pass
        payload = "H2O = H+ + OH-; 1e-14; __import__('os').system('touch %s')" % marker
        result = self.calc.calculate(
            equations=[payload],
            concentrations={},
        )
        self.assertFalse(result["success"])
        self.assertFalse(os.path.exists(marker))

    def test_restricted_globals_still_solves_legit_systems(self):
        """Restricted eval globals must not break legitimate calculations."""
        equations = [
            "H2O = H+ + OH-; 10**-14/55.4",
        ]
        result = self.calc.calculate(
            equations=equations,
            concentrations={"H2O": 55.4},
        )
        self.assertTrue(result["success"])
        self.assertAlmostEqual(result["ph"], 7.0, places=1)

    def test_from_string_always_passes_no_builtins_globals(self):
        """The from_string call always passes SAFE_EVAL_GLOBALS (SEC-02 pin).

        Side-effect tests cannot distinguish a payload blocked by the regex
        gate from one blocked by restricted globals — if the rxn_parse_kwargs
        were ever dropped, every marker test would still pass. This test pins
        the call-site contract directly: the exact SAFE_EVAL_GLOBALS object
        must be bound to globals_, never globals_=False, never missing.
        """
        with patch(
            "chemistry_calculators.calculations.equilibria.EqSystem.from_string",
            return_value=type(
                "FakeEqSystem",
                (),
                {"substances": [], "root": lambda self, c: ([], {}, True)},
            )(),
        ) as m:
            self.calc.calculate(
                equations=["H2O = H+ + OH-; 10**-14/55.4"],
                concentrations={"H2O": 55.4},
            )
        m.assert_called_once()
        kwargs = m.call_args.kwargs["rxn_parse_kwargs"]
        self.assertIs(kwargs["globals_"], security.SAFE_EVAL_GLOBALS)

    def test_engine_validation_rejection_returns_constant(self):
        """Unsafe strings return the fixed constant, never exception text."""
        result = self.calc.calculate(
            equations=["H2O = H+ + OH-; __import__('os')"],
            concentrations={},
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"], "Unsafe or malformed reaction string"
        )

    def test_engine_solver_failure_returns_generic_message(self):
        """Solver failures return the generic constant, never str(e)."""
        with patch(
            "chemistry_calculators.calculations.equilibria.EqSystem.from_string",
            return_value=type(
                "ExplodingEqSystem",
                (),
                {
                    "substances": [],
                    "root": lambda self, c: (_ for _ in ()).throw(
                        RuntimeError("solver exploded")
                    ),
                },
            )(),
        ):
            result = self.calc.calculate(
                equations=["H2O = H+ + OH-; 10**-14/55.4"],
                concentrations={"H2O": 55.4},
            )
        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"], "The equilibrium system could not be solved."
        )

    def test_ka_mode_engine_solve(self):
        """Ka-mode systems solve end-to-end through the engine (coverage corner)."""
        result = self.calc.calculate(
            equations=["CH3COOH = H+ + CH3COO-; 1.75e-5"],
            concentrations={"CH3COOH": 0.1},
        )
        self.assertTrue(result["success"])
        self.assertAlmostEqual(result["ph"], 2.88, places=1)
        self.assertIn("H+", result["species"])


class EquilibriumFormTests(SimpleTestCase):
    """Tests for the EquilibriumSystemForm."""

    def _valid_reactions_json(self):
        """Helper: return a valid reactions JSON string for the default example."""
        return json.dumps([
            {
                "reactants": "HCO3-",
                "products": "H+ + CO3-2",
                "k_mode": "pKa",
                "k_value": "10.3",
            },
            {
                "reactants": "H2CO3",
                "products": "H+ + HCO3-",
                "k_mode": "pKa",
                "k_value": "6.3",
            },
            {
                "reactants": "H2O",
                "products": "H+ + OH-",
                "k_mode": "pKa",
                "k_value": "14.0",
            },
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
            {
                "reactants": "CH3COOH",
                "products": "H+ + CH3COO-",
                "k_mode": "Ka",
                "k_value": "1.75e-5",
            },
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
            {
                "reactants": "",
                "products": "H+ + OH-",
                "k_mode": "pKa",
                "k_value": "14.0",
            },
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

    def test_malformed_reactions_json_generic_copy(self):
        """Malformed reactions JSON emits fixed copy, never parser text (D-06)."""
        form = EquilibriumSystemForm({"reactions": "{not json"})
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Reactions data could not be read.", form.errors["reactions"][0]
        )
        # The parser's parse-position text must never leak (CWE-209).
        self.assertNotIn("Expecting", form.errors["reactions"][0])

    def test_malformed_concentrations_json_generic_copy(self):
        """Malformed concentrations JSON emits fixed copy, never parser text."""
        form = EquilibriumSystemForm({
            "reactions": self._rce_reactions("14.0"),
            "concentrations": "{bad",
        })
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Concentrations data could not be read.",
            form.errors["concentrations"][0],
        )
        self.assertNotIn("Expecting", form.errors["concentrations"][0])

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

    def _rce_reactions(self, k_value, reactants="H2O", products="H+ + OH-"):
        return json.dumps([
            {
                "reactants": reactants,
                "products": products,
                "k_mode": "pKa",
                "k_value": k_value,
            },
        ])

    def test_rce_payload_in_k_value_rejected(self):
        """Arbitrary Python in k_value must be rejected, never evaled."""
        payload = "__import__('os').system('touch /tmp/rce_form_marker')"
        form = EquilibriumSystemForm({"reactions": self._rce_reactions(payload)})
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)
        self.assertFalse(os.path.exists("/tmp/rce_form_marker"))

    def test_rce_semicolon_payload_in_k_value_rejected(self):
        """A ';' smuggled into k_value must not create extra eval segments."""
        payload = "14.0; __import__('os').system('touch /tmp/rce_form_marker2')"
        form = EquilibriumSystemForm({"reactions": self._rce_reactions(payload)})
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)
        self.assertFalse(os.path.exists("/tmp/rce_form_marker2"))

    def test_rce_payload_in_reactants_rejected(self):
        """A ';' smuggled into reactants must be rejected."""
        payload_reactants = "H2O; __import__('os')"
        form = EquilibriumSystemForm({
            "reactions": self._rce_reactions("14.0", reactants=payload_reactants),
        })
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)

    def test_k_value_non_numeric_rejected(self):
        for bad in ("notanumber", "", "10**-3", "1+1", "1/0"):
            form = EquilibriumSystemForm({
                "reactions": self._rce_reactions(bad),
            })
            self.assertFalse(form.is_valid(), bad)
            self.assertIn("reactions", form.errors)

    def test_k_value_non_finite_rejected(self):
        for bad in ("nan", "inf", "1e999"):
            form = EquilibriumSystemForm({
                "reactions": self._rce_reactions(bad),
            })
            self.assertFalse(form.is_valid(), bad)
            self.assertIn("reactions", form.errors)

    def test_oversized_reactions_rejected(self):
        reactions = json.dumps([
            {"reactants": "H2O", "products": "H+ + OH-",
             "k_mode": "pKa", "k_value": "14.0"},
        ] * 2000)
        form = EquilibriumSystemForm({"reactions": reactions})
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)


class EquilibriaViewTests(TestCase):
    """Tests for the CalculateEquilibriaView."""

    def setUp(self):
        self.client = Client()

    def test_equilibria_view_get(self):
        response = self.client.get(reverse("equilibria"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "chemistry_calculators/calculator/equilibria.html"
        )

    def test_equilibria_view_post_valid(self):
        data = {
            "reactions": json.dumps([
                {
                    "reactants": "HCO3-",
                    "products": "H+ + CO3-2",
                    "k_mode": "pKa",
                    "k_value": "10.3",
                },
                {
                    "reactants": "H2CO3",
                    "products": "H+ + HCO3-",
                    "k_mode": "pKa",
                    "k_value": "6.3",
                },
                {
                    "reactants": "H2O",
                    "products": "H+ + OH-",
                    "k_mode": "pKa",
                    "k_value": "14.0",
                },
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

    def test_equilibria_view_rejects_rce_payload(self):
        """A full POST with a malicious k_value must fail validation, no eval."""
        marker = "/tmp/rce_view_marker"
        try:
            os.path.exists(marker) and os.remove(marker)
        except OSError:
            pass
        data = {
            "reactions": json.dumps([
                {
                    "reactants": "H2O",
                    "products": "H+ + OH-",
                    "k_mode": "pKa",
                    "k_value": "__import__('os').system('touch %s')" % marker,
                },
            ]),
            "concentrations": "{}",
            "solvent": "H2O",
            "solvent_concentration": 55.4,
        }
        response = self.client.post(reverse("equilibria"), data)
        self.assertEqual(response.status_code, 200)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)
        self.assertFalse(os.path.exists(marker))

    def test_equilibria_view_rejects_kwargs_path_payload(self):
        """A ';' smuggled into reactants must be rejected at the view layer too.

        Closes the TEST-03 view-layer kwargs gap: the 3-segment kwargs-eval
        path (chempy parsing.py:491) is only reachable via an extra ';'
        segment, so the payload hides it in reactants. The form charset gate
        must reject it before any eval, and no marker file may ever be created.
        """
        marker = "/tmp/rce_view_marker_kwargs"
        try:
            os.path.exists(marker) and os.remove(marker)
        except OSError:
            pass
        data = {
            "reactions": json.dumps([
                {
                    "reactants": (
                        "H2O; x=__import__('os').system('touch %s')" % marker
                    ),
                    "products": "H+ + OH-",
                    "k_mode": "pKa",
                    "k_value": "14.0",
                },
            ]),
            "concentrations": "{}",
            "solvent": "H2O",
            "solvent_concentration": 55.4,
        }
        response = self.client.post(reverse("equilibria"), data)
        self.assertEqual(response.status_code, 200)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertFalse(form.is_valid())
        self.assertIn("reactions", form.errors)
        self.assertFalse(os.path.exists(marker))


class LoggingConfigTests(SimpleTestCase):
    def test_logging_console_handler_configured(self):
        """Ensure LOGGING uses console handler for stdout/stderr."""
        console_handler = settings.LOGGING['handlers']['console']
        self.assertEqual(console_handler['class'], 'logging.StreamHandler')
        self.assertEqual(console_handler['formatter'], 'verbose')
