from django.contrib import messages
from django.http import HttpResponse
from django.views.generic.edit import FormView
from django.views.generic import TemplateView
from .forms import MolecularFormulaForm, ChemicalReaction
from .utils import calculations
from abc import ABC, abstractmethod


class LandingPage(TemplateView):
    """Landing Page

    Args:
        TemplateView (Class): Class Based Django View
    """

    template_name = "landing.html"


class BaseCalculateView(FormView, ABC):
    """
    Abstract base class for views that calculate some result based on a form submission.

    Attributes:
    - template_name (str): The template file path for rendering the view.
    - form_class (Type[Form]): The form class used for user input.
    - success_url (str): The URL to redirect to after a successful form submission.

    Methods:
    - form_valid(form: Form) -> HttpResponse: Overrides the FormView method to handle a valid form submission.
    - process_calculation(form: Form) -> dict: Abstract method to be implemented by subclasses for custom logic.

    Example usage:
    1. Inherit from this class in a concrete view class.
    2. Implement the `process_calculation` method for custom logic.
    3. Add the concrete view class to your `urls.py`.

    This class provides a structure for views that involve form submissions and custom calculations.

    Args:
    - FormView (Type[FormView]): Django's generic view class for handling form submissions.
      Inherits from FormView to utilize form handling functionality.
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
    Concrete view class for calculating the molecular weight based on a submitted molecular formula.

    Args:
    - BaseCalculateView (Type[BaseCalculateView]): Base class for views that involve form submissions and custom calculations.

    Returns:
    - dict: The result of the molecular weight calculation, including name, formula, and molecular weight.
    """

    template_name = "calculator/molecular_weight.html"
    form_class = MolecularFormulaForm

    def process_calculation(self, form: MolecularFormulaForm) -> dict:
        """
        Calculate the molecular weight based on the submitted molecular formula.

        Parameters:
        - form (MolecularFormulaForm): The form instance containing the molecular formula.

        Returns:
        - dict: The result of the molecular weight calculation, formula, and molecular weight.
        """
        molecule = form.cleaned_data
        molecular_weight = calculations.calculate_molecular_weight(molecule["formula"])
        result = {"formula": molecule["formula"], "molecular_weight": molecular_weight}
        return result


class BalanceChemicalReaction(BaseCalculateView):
    """_summary_

    Args:
        BaseCalculateView (_type_): _description_

    Returns:
        _type_: _description_
    """

    template_name = "calculator/reaction_balancer.html"
    form_class = ChemicalReaction

    def process_calculation(self, form: form_class) -> str:
        """
        Process a chemical reaction calculation based on the input form.

        This method takes a Django form instance as input, extracts the necessary data, and performs the following steps:
        1. Extracts the cleaned data from the form, including reactants, products, and the reversible attribute for the chemical reaction.
        2. Parses the reactants and products to obtain individual molecules.
        3. Balances the chemical reaction using chempy.balance_chemical_reaction method in utils.py.
        4. Transforms the balanced reaction into a string representation of the chemical equation.

        Args:
            form (form_class): A Django form instance representing the user input for a chemical reaction.

        Returns:
            str: A string representation of the balanced chemical reaction equation.

        Example:
            Given a form with reactants {'H2', 'O2'}, products {'H2O'}, and reversible=False,
            the method may return "2H2 + O2 -> 2H2O" representing the balanced reaction.
        """
        try:
            # Clean the input to obtain the data
            reaction = form.cleaned_data

            # Extract reversible attribute
            reversible = reaction["reversible"]

            # Parse the fields 'reactant' and 'product' to obtain individual molecules
            reactancts = [x.strip() for x in reaction["reactant"].split("+")]
            products = [x.strip() for x in reaction["product"].split("+")]

            # Make a dictionary with only the molecules' molecular formulas as the keys
            reactancts_dict = {reactant for reactant in reactancts}
            products_dict = {product for product in products}

            # Balance the reaction with chempy.balance_chemical_reaction in utils.py
            (
                reactancts_balanced,
                products_balanced,
            ) = calculations.balance_chemical_reaction(
                reactants=reactancts_dict, products=products_dict
            )

            # Transform the output of balance_chemical_reaction to a string representing the equation
            result = calculations.balance_chemical_reaction_to_latex(
                reactancts_balanced, products_balanced, reversible
            )

            return result
        except Exception as e:
            # Handles the exception to be displayed as an error message to the user trough django.messages
            messages.error(self.request, f"Error: {str(e)}")
            return {"error": str(e)}
