from django import forms
from .utils.calculations import convert_volume


class MolecularFormulaForm(forms.Form):
    """
    A Django form for capturing a molecular formula.

    Parameters:
    - formula (CharField): A character field for the molecular formula of one or more molecules.
                          The maximum length is set to 100 characters.

    Example usage:
    >>> form_class = MoleculeFormulaForm(data={'formula': 'NH4+ CO2 H2O'})
    >>> form.is_valid()
    True
    >>> form.cleaned_data
    {'formula': 'NH4+ CO2 H2O'}
    """

    formula = forms.CharField(
        max_length=100,
        label="Chemical Formula",
        help_text="Enter the chemical formula of all desired substances separated by a white space.",
        widget=forms.TextInput(attrs={"placeholder": "NH4+ CO2 H2O"}),
        required=True,
    )


class ChemicalReactionForm(forms.Form):
    """
    A Django form for capturing a molecular reaction.
    Products and reactants are meant to be separated with a whitespace.

    Attributes:
        reactant (CharField): A field for capturing the reactants in the chemical reaction.
                             Max length is set to 100 characters.
                             Example: 'H2 O2'.

        product (CharField): A field for capturing the products in the chemical reaction.
                             Max length is set to 100 characters.
                             Example: 'H2O'.

        reversible (BooleanField): A checkbox indicating whether the chemical reaction is reversible.
                                   Defaults to True (reversible).
                                   Example: Checked (Reversible) or Unchecked (Non-reversible).
    """

    reactant = forms.CharField(
        max_length=100,
        label="Reactant(s)",
        help_text="Enter the chemical formula for all of the reactants in this input box separated by a whitespace.",
        widget=forms.TextInput(attrs={"placeholder": "H2 O2"}),
    )
    product = forms.CharField(
        max_length=100,
        label="Product(s)",
        help_text="Enter the chemical formula for all of the reactants in this input box separated by a whitespace.",
        widget=forms.TextInput(attrs={"placeholder": "H2O"}),
    )
    reversible = forms.BooleanField(
        initial=True,
        required=False,
        label="Irreversible Reaction",
        help_text="Uncheck this box if the reaction is not reversible",
    )

    def clean(self):
        """
        Cleans and parses the input data to obtain reactants and products separately.

        Raises:
            forms.ValidationError: Raised if neither reactant nor product is provided.

        Returns:
            dict: A dictionary containing the cleaned and parsed data.
        """
        cleaned_data = super().clean()
        reactant = cleaned_data.get("reactant")
        product = cleaned_data.get("product")

        if not reactant and not product:
            raise forms.ValidationError("Reactant and product must be provided.")

        return cleaned_data


class SolutionForm(forms.Form):
    """
    Django form for capturing a simple dilution with an initial and final state.

    This form allows users to input details about a dilution, including solute,
    solvent, initial and final concentrations, and initial and final volumes.

    Despite the fact that both concentration_initial and volume_initial are optional,
    only one of those fields can be empty. Otherwise, the form will raise an error.

    Args:
        forms (Django forms): Django forms module for form creation.

    Raises:
        forms.ValidationError: Raised if validation fails during form cleaning.

    Returns:
        dict: Cleaned and validated form data.

    Example:
        Usage in a Django form view:

        >>> form = SolutionForm(request.POST)
        >>> if form.is_valid():
        >>>     # Process the cleaned and validated data
        >>>     cleaned_data = form.cleaned_data

    Attributes:
        CONCENTRATION_CHOICES (tuple): Choices for concentration units.
        VOLUME_CHOICES (tuple): Choices for volume units.
        solute(CharField): A field for capturing the chemical formula solute of the solution. Optional.
        solvent(CharFiled): A field for capturing the chemical formula of the solvent of the solution. Optional.
        concentration_initial(FloatField): A field for capturing the initial concentration of the solution. Optional.
        volume_initial(Floatfiled): A field for capturing the initial volume of the solution. Optional.


    Methods:
        multiply_by_unit(value, unit): Helper method to multiply a value by its corresponding unit.
        clean(): Clean and validate form data, including checks for the relationship between
                initial and final concentrations.

    """
    
    # Prefix values for units
    BASE = 1e0
    MILLI = 1e-3
    MICRO = 1e-6
    NANO = 1e-9
    PICO = 1e-12
    
    # Define choices for concentration units
    CONCENTRATION_CHOICES = [
        (BASE, "mol/L"),
        (MILLI, "mmol/L"),
        (MICRO, "μmol/L"),
        (NANO, "nmol/L"),
        (PICO, "pmol/L"),
    ]

    # Define choices for volume units
    VOLUME_CHOICES = [
        (BASE, "L"),
        (MILLI, "mL"),
        (MICRO, "μL"),
        (NANO, "nL"),
        (PICO, "pL"),
    ]

    solute = forms.CharField(
        max_length=100,
        required=False,
        help_text="Optional: Enter the chemical formula of the solute.",
        widget=forms.TextInput(attrs={"placeholder": "NaOH"}),
    )
    solvent = forms.CharField(
        max_length=100,
        required=False,
        help_text="Optional: Enter the chemical formula of the solvent.",
        widget=forms.TextInput(attrs={"placeholder": "H2O"}),
    )
    c1 = forms.FloatField(
        min_value=0,
        label="Initial concentration of the solute.",
        required=False,
        help_text="Enter the concentration of the initial solute in the solution and choose  the corresponding concentration unit prefix the menu.",
    )

    v1 = forms.FloatField(
        min_value=0,
        label="Initial volume of the solute.",
        required=False,
        help_text="Enter the volume of the initial solution and choose the corresponding volume unit prefix in the menu.",
    )
    c2 = forms.FloatField(
        min_value=0,
        label="Final concentration of the solution.",
        required=False,
        help_text="Enter the concentration of the final solution.",
    )
    v2 = forms.FloatField(
        min_value=0,
        label="Final volume of the solution.",
        required=False,
        help_text="Enter the volume of the final solution.",
    )
    c1_unit = forms.ChoiceField(
        required=False,
        choices=CONCENTRATION_CHOICES,
        label="Initial concentration scale unit.",
        initial="mol/L",
        help_text="Choose a concentration unit for the initial concentration of the solute in the solution.",
    )
    c2_unit = forms.ChoiceField(
        required=False,
        choices=CONCENTRATION_CHOICES,
        label="Final concentration scale unit.",
        initial="mol/L",
        help_text="Choose a concentration unit for the final concentration of the solute in the solution.",
    )
    v1_unit = forms.ChoiceField(
        required=False,
        choices=VOLUME_CHOICES,
        label="Initial volume scale unit.",
        initial="mL",
        help_text="Choose a volume unit for the initial volume of the solution.",
    )
    v2_unit = forms.ChoiceField(
        required=False,
        choices=VOLUME_CHOICES,
        label="Final volume scale unit",
        initial="mL",
        help_text="Choose a volume unit for the final volume of the solution.",
    )
   
