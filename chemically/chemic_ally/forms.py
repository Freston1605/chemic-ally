from django import forms
from .utils.calculations import convert_volume


class MolecularFormulaForm(forms.Form):
    """
    A Django form for capturing a molecular formula.

    Parameters:
    - formula (CharField): A character field for the molecular formula.
                          The maximum length is set to 100 characters.

    Example usage:
    >>> form_class = MoleculeFormulaForm(data={'formula': 'H2O'})
    >>> form.is_valid()
    True
    >>> form.cleaned_data
    {'formula': 'H2O'}
    """

    formula = forms.CharField(
        max_length=100,
        label="Chemical Formula",
        help_text="Enter the chemical formula of the desired substance.",
        widget=forms.TextInput(attrs={"placeholder": "NH4+"}),
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

    # Define choices for concentration units
    CONCENTRATION_CHOICES = [
        ("mol/L", "Molar", 1e0),
        ("mmol/L", "Millimolar", 1e-3),
        ("umol/L", "Micromolar", 1e-6),
        ("nmol/L", "Nanomolar", 1e-9),
        ("pmol/L", "Picomolar", 1e-12),
    ]

    # Define choices for volume units
    VOLUME_CHOICES = [
        ("L", "Liters", 1e0),
        ("mL", "Milliliters", 1e-3),
        ("μL", "Microliters", 1e-6),
        ("nL", "Nanoliters", 1e-9),
        ("pL", "Picoliters", 1e-12),
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
    conc_inital = forms.FloatField(
        min_value=0,
        label="Initial concentration of the solution.",
        required=False,
        help_text="Enter the concentration of the initial solution.",
    )

    vol_initial = forms.FloatField(
        min_value=0,
        label="Initial volume of the solution.",
        required=False,
        help_text="Enter the volume of the initial solution.",
    )
    conc_final = forms.FloatField(
        min_value=0,
        label="Final concentration of the solution.",
        required=True,
        help_text="Enter the concentration of the final solution.",
    )
    vol_final = forms.FloatField(
        min_value=0,
        label="Final volume of the solution.",
        required=True,
        help_text="Enter the volume of the final solution.",
    )
    conc_initial_unit = forms.ChoiceField(
        required=False,
        choices=CONCENTRATION_CHOICES,
        label="Initial concentration scale unit.",
        initial="mol/L",
        help_text="Choose a concentration unit for the initial concentration of the solute in the solution.",
    )
    conc_final_unit = forms.ChoiceField(
        required=False,
        choices=CONCENTRATION_CHOICES,
        label="Final concentration scale unit.",
        initial="mol/L",
        help_text="Choose a concentration unit for the final concentration of the solute in the solution.",
    )
    vol_initial_unit = forms.ChoiceField(
        required=False,
        choices=VOLUME_CHOICES,
        label="Initial volume scale unit.",
        initial="mL",
        help_text="Choose a volume unit for the initial volume of the solution.",
    )
    vol_final_unit = forms.ChoiceField(
        required=False,
        choices=VOLUME_CHOICES,
        label="Final volume scale unit",
        initial="mL",
        help_text="Choose a volume unit for the final volume of the solution.",
    )

    def clean(self):
        """
        Clean and validate form data.

        This method performs additional validation on the form data, including checks
        for the relationship between initial and final concentrations.

        Raises:
            forms.ValidationError: Raised if validation fails, with details on the error.

        Returns:
            dict: Cleaned and validated form data.

        Example:
            Usage in a Django form view:

            >>> form = SolutionForm(request.POST)
            >>> if form.is_valid():
            >>>     # Process the cleaned and validated data
            >>>     cleaned_data = form.cleaned_data
        """

        cleaned_data = super().clean()
        # Get the initial and final concentration
        concentration_initial = cleaned_data.get("concentration_initial")
        # concentration_final = cleaned_data.get("concentration_final")

        # Get the initial and final volume
        volume_initial = cleaned_data.get("volume_initial")
        # volume_final = cleaned_data.get("volume_final")

        # Check if either concentration_intial of volume_initial are provided, but not both
        if concentration_initial is not None and volume_initial is not None:
            raise forms.ValidationError(
                "Please provide either the initial concentration or the initial volume, but not both."
            )

        # # Get the initial and final concentration units
        # concentration_initial_unit = cleaned_data.get("concentration_initial_unit")[2]
        # concentration_final_unit = cleaned_data.get("concentration_final_unit")[2]

        # # Multiply values by their units
        # concentration_initial = self.multiply_by_unit(
        #     concentration_initial, concentration_initial_unit
        # )
        # concentration_final = self.multiply_by_unit(
        #     concentration_final, concentration_final_unit
        # )

        # # Check if the final volume is greater than the initial volume
        # if concentration_initial is not None and concentration_final is not None:
        #         if concentration_final > concentration_initial:
        #             raise forms.ValidationError(
        #                 "Final concentration cannot exceed initial concentration."
        #             )
        return cleaned_data
