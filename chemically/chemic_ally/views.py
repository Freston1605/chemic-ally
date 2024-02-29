from abc import ABC, abstractmethod
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.views.generic.edit import FormView
from django.views.generic import TemplateView
from .forms import MolecularFormulaForm, ChemicalReactionForm, SolutionForm
from .utils import calculations


class LandingPage(TemplateView):
    """
    Class based view for the landing page of the website.

    Args:
        TemplateView (Class): Class Based Django View
    """

    template_name = "chemic_ally/landing.html"


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
    Concrete view class for calculating molecular weights based on a submitted molecular formula.

    This view calculates the molecular weight for each molecular formula submitted via a form,
    parsing the input string and looping over each formula to calculate its molecular weight.

    Attributes:
        template_name (str): The name of the template to render.
        form_class (Type[Form]): The form class to use for input validation and submission.

    Methods:
        process_calculation(form: MolecularFormulaForm) -> dict:
            Calculate the molecular weight based on the submitted molecular formula.
            Parses the string containing one or more molecular formulas separated by whitespace,
            calculates the molecular weight for each formula, and returns a dictionary
            with each formula as key and its corresponding molecular weight as value.

    Args:
        BaseCalculateView (Type[BaseCalculateView]): Base class for views that involve form submissions
            and custom calculations.

    """

    template_name = "chemic_ally/calculator/molecular_weight.html"
    form_class = MolecularFormulaForm

    def process_calculation(self, form: MolecularFormulaForm) -> dict:
        """
        Calculate the molecular weight based on the submitted molecular formula.

        Parameters:
            form (MolecularFormulaForm): The form instance containing the molecular formula.

        Returns:
            dict: A dictionary containing each molecular formula and its corresponding calculated
                molecular weight.
        """

        # Obtain the molecular formulas from the form and parse them into a list
        molecules = [x.strip() for x in form.cleaned_data["formula"].split()]
        result = {}
        # Loop over the molecules list
        for molecule in molecules:
            # Calculate the molecular weight
            molecular_weight = calculations.calculate_molecular_weight(molecule)

            # Check if the molecular weight is valid
            if molecular_weight is not None:
                # Add the molecule:molecular_weight pair to the result dictionary
                result[molecule] = molecular_weight
                
        return result


class BalanceChemicalReaction(BaseCalculateView):
    """
    Concrete view class for handling a chemical reaction from the django form ChemicalReaction.
    It is intended to be used in the context of stoichiomtetric coefficient balancing for a particular chemical reaction.

    Args:
        - BaseCalculateView (Type[BaseCalculateView]): Base class for views that involve form submissions and custom calculations.
    """

    template_name = "chemic_ally/calculator/reaction_balancer.html"
    form_class = ChemicalReactionForm

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
        """
        try:
            # Clean the input to obtain the data
            reaction = form.cleaned_data

            # Extract reversible attribute
            reversible = reaction["reversible"]

            # Parse the fields 'reactant' and 'product' to obtain individual molecules
            reactancts = [x.strip() for x in reaction["reactant"].split()]
            products = [x.strip() for x in reaction["product"].split()]

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


class CalculateDilutionView(BaseCalculateView):
    """
    A view for calculating dilutions in chemistry.
    Inherits from BaseCalculateView.
    """

    template_name = "chemic_ally/calculator/dilution.html"
    form_class = SolutionForm

    def process_calculation(self, form: form_class) -> dict:
        """
        Processes the calculation based on user input from the form to calculate a simple dilution.
        

        This function performs the following steps:
        1. Obtains cleaned data from the provided form, including initial and final concentrations, volumes, and their respective unit prefixes.
        2. Converts unit prefixes to floating-point values and retrieves corresponding unit labels.
        3. Validates the provided data:
        - Ensures that exactly three out of the four properties (initial concentration, initial volume, final concentration, final volume) are provided.
        - Checks that all provided values are greater than zero.
        4. Checks consistency between initial and final volumes and concentrations.
        5. Calculates the missing property using the provided data.
        6. Converts the calculated value back to its original unit.
        7. Constructs a dictionary containing the missing property, its calculated value, and its corresponding unit label.
        8. Returns the result dictionary.

        Args:
            form (form_class): The form containing user input data.

        Raises:
            ValidationError: If validation fails due to incorrect or insufficient input.
            ValidationError: If consistency checks between initial and final volumes/concentrations fail.
            Exception: If any other unexpected error occurs during the calculation.

        Returns:
            dict: A dictionary containing the result of the calculation, including the missing property, its calculated value, and its unit label.
        """


        try:
            # Get the cleaned data from the form
            solution = form.cleaned_data

            # Solution properties
            c1 = solution["c1"]
            v1 = solution["v1"]
            c2 = solution["c2"]
            v2 = solution["v2"]

            # Store and convert to a float the  value of the unit prefixes for every property
            c1_unit = float(solution["c1_unit"])
            v1_unit = float(solution["v1_unit"])
            c2_unit = float(solution["c2_unit"])
            v2_unit = float(solution["v2_unit"])
            
            print("Test")

            # Get the selected unit labels from the form's cleaned data
            c1_unit_label = dict(form.fields["c1_unit"].choices)[c1_unit]
            v1_unit_label = dict(form.fields["v1_unit"].choices)[v1_unit]
            c2_unit_label = dict(form.fields["c2_unit"].choices)[c2_unit]
            v2_unit_label = dict(form.fields["v2_unit"].choices)[v2_unit]
            
            print("Test 2")

            properties = {
                "c1": (c1, c1_unit, "Initial Concentration", c1_unit_label),
                "v1": (v1, v1_unit, "Initial Volume", v1_unit_label),
                "c2": (c2, c2_unit, "Final Concentration", c2_unit_label),
                "v2": (v2, v2_unit, "Final Volume", v2_unit_label),
            }
            
            # Print every variable for debugging
            for key, value in properties.items():
                print(f"{key}: {value}")
            
            # Count the number of non-None values
            non_none_count = sum(
                1 for value, _, _, _ in properties.values() if value is not None
            )

            # Check if the count of provided values is exactly three
            if non_none_count != 3:
                raise ValidationError(
                    "Exactly three values among Initial Concentration, Initial Volume, Final Concentration, and Final Volume must be provided.",
                    code="invalid",
                )

            # Loop over the properties dictionary
            for prop, (value, unit, label, unit_label) in properties.items():
                # Check if the current value of the property is not None
                if value is not None:
                    # Check if the current value is higher than zero, raise a ValidationError if True
                    if value <= 0:
                        raise ValidationError(
                            f"The {label.lower()} cannot be equal or lower than zero.",
                            code="invalid",
                        )
                    # Else, multiply the value by its unit to obtain standard units in Liters and Molar
                    locals()[prop] *= unit
                else:
                    # If the value is None, store it and its unit as the missing propery to calculate
                    # This only stores one label and unit, as the previous code enforces that only one property is missing
                    missing_property = label
                    missing_unit_value = unit
                    missing_unit_label = unit_label
                    
                    print("Missing property", missing_property)
                    print("Unit value", missing_unit_value)
                    print("Unit label", missing_unit_label)

            # Check for consistency in concentration and volume
            # The final states of the solution must have either lower concentration or higher volume than its initial state

            # Check if initial volume v1 is greater or equal than final volume v2
            if properties["v1"][0] is not None and properties["v2"][0] is not None:
                if properties["v1"][0] >= properties["v2"][0]:
                    # Raise a ValidationError if True
                    raise ValidationError(
                        "Initial volume cannot be greater or equal than final volume.",
                        code="invalid",
                    )
                missing_value = calculations.calculate_dilution(
                    *[properties[prop][0] for prop in ["c1", "v1", "c2", "v2"]]
                )

            # Check if initial concentration c1 is greater than final concentration c2
            elif properties["c1"][0] is not None and properties["c2"][0] is not None:
                if properties["c1"][0] < properties["c2"][0]:
                    raise ValidationError(
                        "Initial concentration cannot be lesser than final concentration.",
                        code="invalid",
                    )
                missing_value = calculations.calculate_dilution(
                    *[properties[prop][0] for prop in ["c1", "v1", "c2", "v2"]]
                )
            else:
                raise ValidationError(
                    "Either the initial and final volume or the initial and final concentration must be provided.",
                    code="required",
                )

            # Convert back into the original unit
            missing_value = missing_value / missing_unit_value

            # Return the corresponding property and its value
            result = {
                "property": missing_property,
                "value": missing_value,
                "unit": missing_unit_label,
            }
            return result

        except Exception as e:
            # Handles the exception to be displayed as an error message to the user trough django.messages
            messages.error(self.request, f"Error: {e}")
