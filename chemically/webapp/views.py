from abc import ABC, abstractmethod

from django.contrib import messages
from django.http import HttpResponse
from django.views.generic import TemplateView
from django.views.generic.edit import FormView
from django.views import View
from django.shortcuts import render, redirect

from .calculations.base import (
    DilutionCalculator,
    MolecularWeightCalculator,
    ReactionBalancer,
)
from .forms import ChemicalReactionForm, MolecularFormulaForm, SolutionForm
from .utils import add_previous_substances
import pint


class LandingPage(TemplateView):
    """
    Class based view for the landing page of the website.

    Args:
        TemplateView (Class): Class Based Django View
    """

    template_name = "webapp/landing.html"


class BaseCalculateView(FormView, ABC):
    """
    Abstract base class for views that calculate some result based on a form submission.

    Attributes:
    - template_name (str): The template file path for rendering the view.
    - form_class (Type[Form]): The form class used for user input.
    - success_url (str): The URL to redirect to after a successful form submission.

    Methods:
    - form_valid(form: Form) -> HttpResponse:
        Overrides the FormView method to handle a valid form submission.
    - process_calculation(form: Form) -> dict:
        Abstract method implemented by subclasses for custom logic.

    Example usage:
    1. Inherit from this class in a concrete view class.
    2. Implement the `process_calculation` method for custom logic.
    3. Add the concrete view class to your `urls.py`.

    This class provides a structure for views that involve form submissions and
    custom calculations.

    Args:
    - FormView (Type[FormView]): Django's generic view class for handling form
      submissions. Inherits from FormView to utilize form handling
      functionality.
    - ABC (Type[ABC]): Abstract Base Class from the 'abc' module.
      Inherits from ABC to define an abstract method, process_calculation,
      which must be implemented by subclasses.
    """

    template_name = ""  # Set this in subclasses
    form_class = None  # Set this in subclasses
    success_url = ""  # Set this in subclasses

    def form_valid(self, form: form_class) -> HttpResponse:
        """
        Handle a valid form submission.

        Parameters:
        - form (Form): The validated form instance.

        Returns:
        - HttpResponse: Rendered template with the form and result.
        """
        result = self.process_calculation(form)
        return self.render_to_response(self.get_context_data(form=form, result=result))

    @abstractmethod
    def process_calculation(self, form: form_class) -> dict:
        """
        Abstract method to be implemented by subclasses for custom logic.

        Parameters:
        - form (Form): The form instance.

        Returns:
        - dict: The result of the custom calculation.
        """


class CalculateMolecularWeightView(BaseCalculateView):
    """
    Concrete view class for calculating molecular weights from a submitted
    molecular formula.

    This view calculates the molecular weight for each formula submitted via a
    form, parsing the input string and looping over each molecule.

    Attributes:
        template_name (str): The name of the template to render.
        form_class (Type[Form]): The form class used for input validation and
        submission.

    Methods:
        process_calculation(form: MolecularFormulaForm) -> dict:
            Calculate the molecular weight based on the submitted formula.
            Parses the string containing one or more formulas separated by
            whitespace and returns a dictionary with each formula mapped to its
            molecular weight.

    Args:
        BaseCalculateView (Type[BaseCalculateView]):
            Base class for views that involve form submissions and custom
            calculations.

    """

    template_name = "webapp/calculator/molecular_weight.html"
    form_class = MolecularFormulaForm

    def process_calculation(self, form: MolecularFormulaForm) -> dict:
        """
        Calculate the molecular weight based on the submitted molecular formula.

        Parameters:
            form (MolecularFormulaForm): The form instance containing the
            molecular formula.

        Returns:
            dict: A dictionary mapping each formula to its calculated molecular
            weight.
        """

        # Obtain the molecular formulas from the form and parse them into a list
        molecules = [x.strip() for x in form.cleaned_data["formula"].split()]
        result = {}
        calculator = MolecularWeightCalculator()
        # Loop over the molecules list
        for molecule in molecules:
            # Calculate the molecular weight
            molecular_weight = calculator.calculate(molecule)

            # Check if the molecular weight is valid
            if molecular_weight is not None:
                # Add the molecule:molecular_weight pair to the result dictionary
                result[molecule] = molecular_weight
        if result:
            add_previous_substances(self.request, result.keys())

        return result


class BalanceChemicalReaction(BaseCalculateView):
    """
    Concrete view for balancing chemical reactions.

    Part of the new class-based calculator framework, this view delegates the
    actual stoichiometric balancing to :class:`ReactionBalancer` defined in
    ``webapp.calculations.base``.

    Args:
        - BaseCalculateView (Type[BaseCalculateView]): Base class for form-driven
          calculations.
    """

    template_name = "webapp/calculator/reaction_balancer.html"
    form_class = ChemicalReactionForm

    def process_calculation(self, form: form_class) -> str:
        """
        Perform stoichiometric balancing for the submitted reaction.

        The cleaned form data is parsed into lists of reactant and product
        formulas. It is passed to the :class:`ReactionBalancer` calculator from
        the class-based API. The balanced equation is returned as a LaTeX string.

        Args:
            form (form_class):
                A Django form instance representing the user input for a
                chemical reaction.

        Returns:
            str: A string representation of the balanced chemical reaction equation.
        """
        try:
            reaction = form.cleaned_data
            reversible = reaction["reversible"]
            reactants = [x.strip() for x in reaction["reactant"].split()]
            products = [x.strip() for x in reaction["product"].split()]
            reactants_dict = {reactant for reactant in reactants}
            products_dict = {product for product in products}
            calculator = ReactionBalancer()
            (
                reactants_balanced,
                products_balanced,
            ) = calculator.calculate(reactants=reactants_dict, products=products_dict)
            result = ReactionBalancer.to_latex(
                reactants_balanced, products_balanced, reversible
            )
            add_previous_substances(self.request, reactants + products)
            return result
        except Exception as e:
            messages.error(self.request, f"Error: {str(e)}")


class CalculateDilutionView(BaseCalculateView):
    """
    A view for calculating dilutions in chemistry.
    Inherits from BaseCalculateView.
    """

    template_name = "webapp/calculator/dilution.html"
    form_class = SolutionForm

    def process_calculation(self, form: SolutionForm):
        """
        Processes the calculation based on user input from the form to calculate
        a simple dilution with full error handling and compatibility for
        Pint-based logic.

        Returns a result dict with the missing property, its value, and its
        unit label. Handles ValidationError for user input issues and general
        Exception for all other cases, showing errors via Django's messages
        framework.
        """
        cd = form.cleaned_data

        try:
            molecular_weight = cd.get("molecular_weight")
            solute_formula = cd.get("solute")

            from .utils.units import Q_

            unit_map = {
                "c1": cd.get("c1_unit"),
                "v1": cd.get("v1_unit"),
                "c2": cd.get("c2_unit"),
                "v2": cd.get("v2_unit"),
            }
            quantity_map = {
                k: Q_(cd[k], unit_map[k])
                for k in ["c1", "v1", "c2", "v2"]
                if cd.get(k) is not None
            }

            missing_prop = next(
                k for k in ["c1", "v1", "c2", "v2"] if cd.get(k) is None
            )

            calculator = DilutionCalculator()
            result = calculator.calculate(
                c1=quantity_map.get("c1"),
                c1_unit=unit_map["c1"],
                v1=quantity_map.get("v1"),
                v1_unit=unit_map["v1"],
                c2=quantity_map.get("c2"),
                c2_unit=unit_map["c2"],
                v2=quantity_map.get("v2"),
                v2_unit=unit_map["v2"],
                molecular_weight=molecular_weight,
                solute_formula=solute_formula,
            )

            if result is None or "missing_value" not in result:
                messages.error(
                    self.request,
                    "Calculation failed. Please check your input.",
                )
                return None

            missing_value = result["missing_value"]
            if hasattr(missing_value, "to_compact"):
                missing_value = missing_value.to_compact()

            labels = [
                "Initial Concentration",
                "Initial Volume",
                "Final Concentration",
                "Final Volume",
            ]
            unit_label_map = {
                "c1": dict(form.fields["c1_unit"].choices)[unit_map["c1"]],
                "v1": dict(form.fields["v1_unit"].choices)[unit_map["v1"]],
                "c2": dict(form.fields["c2_unit"].choices)[unit_map["c2"]],
                "v2": dict(form.fields["v2_unit"].choices)[unit_map["v2"]],
            }
            result_dict = {
                "property": labels[["c1", "v1", "c2", "v2"].index(missing_prop)],
                "value": (
                    float(missing_value.magnitude)
                    if hasattr(missing_value, "magnitude")
                    else float(missing_value)
                ),
                "unit": unit_label_map[missing_prop],
            }

            if "mass_g" in result and result["mass_g"] is not None:
                result_dict["solute_mass_g"] = float(result["mass_g"])

            if solute_formula:
                add_previous_substances(self.request, [solute_formula])

            return result_dict

        except Exception as e:
            import logging

            logging.exception("Dilution calculation failed:")
            messages.error(self.request, f"Calculation error: {str(e)}")
            return None


def make_json_safe(obj):
    if isinstance(obj, pint.Quantity):
        return float(obj.magnitude)
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_json_safe(x) for x in obj]
    return obj


class DashboardView(View):
    template_name = "webapp/dashboard.html"

    def get(self, request):
        # Render the dashboard with any session-persisted data
        context = request.session.get('dashboard_context', {})
        return render(request, self.template_name, context)

    def post(self, request):
        context = {}
        action = request.POST.get('action')
        # Always repopulate fields
        context['formula'] = request.POST.get('formula', '')
        context['c1'] = request.POST.get('c1', '')
        context['c1_unit'] = request.POST.get('c1_unit', 'mol/L')
        context['v1'] = request.POST.get('v1', '')
        context['v1_unit'] = request.POST.get('v1_unit', 'L')
        context['c2'] = request.POST.get('c2', '')
        context['c2_unit'] = request.POST.get('c2_unit', 'mol/L')
        context['v2'] = request.POST.get('v2', '')
        context['v2_unit'] = request.POST.get('v2_unit', 'L')
        context['molecular_weight'] = request.POST.get('molecular_weight', '')
        context['solute'] = request.POST.get('solute', '')
        context['reactant'] = request.POST.get('reactant', '')
        context['product'] = request.POST.get('product', '')
        context['reversible'] = bool(request.POST.get('reversible'))

        if action == 'calc_mw':
            formula = context['formula']
            mw_result = None
            if formula:
                calculator = MolecularWeightCalculator()
                mw_result = calculator.calculate(formula)
            context['mw_result'] = mw_result
        elif action == 'use_mw':
            formula = context['formula']
            calculator = MolecularWeightCalculator()
            mw_result = calculator.calculate(formula)
            context['molecular_weight'] = mw_result
            context['mw_result'] = mw_result
        elif action == 'calc_dilution':
            try:
                calculator = DilutionCalculator()
                result = calculator.calculate(
                    c1=float(context['c1']) if context['c1'] else None,
                    c1_unit=context['c1_unit'],
                    v1=float(context['v1']) if context['v1'] else None,
                    v1_unit=context['v1_unit'],
                    c2=float(context['c2']) if context['c2'] else None,
                    c2_unit=context['c2_unit'],
                    v2=float(context['v2']) if context['v2'] else None,
                    v2_unit=context['v2_unit'],
                    molecular_weight=(
                        float(context['molecular_weight'])
                        if context['molecular_weight']
                        else None
                    ),
                    solute_formula=context['solute'],
                )
                context['dilution_result'] = make_json_safe(result)
            except Exception as e:
                context['dilution_result'] = {
                    'property': 'Error',
                    'value': str(e),
                    'unit': '',
                }
        elif action == 'balance_reaction':
            try:
                reactants = (
                    [x.strip() for x in context['reactant'].split()]
                    if context['reactant']
                    else []
                )
                products = (
                    [x.strip() for x in context['product'].split()]
                    if context['product']
                    else []
                )
                calculator = ReactionBalancer()
                balanced = calculator.calculate(
                    reactants=reactants,
                    products=products,
                )
                if balanced:
                    reactants_balanced, products_balanced = balanced
                    result = ReactionBalancer.to_latex(
                        reactants_balanced,
                        products_balanced,
                        context['reversible'],
                    )
                    context['reaction_result'] = result
                else:
                    context['reaction_result'] = 'Could not balance reaction.'
            except Exception as e:
                context['reaction_result'] = f'Error: {e}'
        elif action == 'reset':
            request.session['dashboard_context'] = {}
            return redirect('dashboard')

        # Save context for persistence, making it JSON-safe
        safe_context = make_json_safe(context)
        request.session['dashboard_context'] = safe_context
        return render(request, self.template_name, context)
