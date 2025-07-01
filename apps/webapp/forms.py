from django import forms


class MolecularFormulaForm(forms.Form):
    """
    A Django form for capturing a molecular formula.

    Parameters:
    - formula (CharField):
        A character field for the molecular formula of one or more molecules.
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
        help_text=(
            "Enter the chemical formula of all desired substances separated "
            "by a white space."
        ),
        widget=forms.TextInput(attrs={"placeholder": "NH4+ CO2 H2O"}),
        required=True,
    )


class ChemicalReactionForm(forms.Form):
    """
    A Django form for capturing a molecular reaction.
    Products and reactants are meant to be separated with a whitespace.

    Attributes:
        reactant (CharField):
            Field for capturing the reactants in the chemical reaction.
            Max length is set to 100 characters. Example: 'H2 O2'.

        product (CharField):
            Field for capturing the products in the chemical reaction.
            Max length is set to 100 characters. Example: 'H2O'.

        reversible (BooleanField):
            Checkbox indicating whether the reaction is reversible.
            Defaults to True (reversible). Example: Checked or unchecked.
    """

    reactant = forms.CharField(
        max_length=100,
        label="Reactant(s)",
        help_text=(
            "Enter the chemical formula for all reactants separated "
            "by a whitespace."
        ),
        widget=forms.TextInput(attrs={"placeholder": "H2 O2"}),
    )
    product = forms.CharField(
        max_length=100,
        label="Product(s)",
        help_text=(
            "Enter the chemical formula for all reactants separated "
            "by a whitespace."
        ),
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
        >>>     # Process cleaned and validated data
        >>>     cleaned_data = form.cleaned_data

    Attributes:
        CONCENTRATION_CHOICES (tuple): Choices for concentration units.
        VOLUME_CHOICES (tuple): Choices for volume units.
        solute(CharField):
            Field for the chemical formula of the solute. Optional.
        solvent(CharFiled):
            Field for the chemical formula of the solvent. Optional.
        concentration_initial(FloatField):
            Initial concentration of the solution. Optional.
        volume_initial(Floatfiled):
            Initial volume of the solution. Optional.


    Methods:
        multiply_by_unit(value, unit):
            Helper to multiply a value by its unit.
        clean():
            Validate form data, including the relationship between the
            initial and final concentrations.

    """

    CONCENTRATION_CHOICES = [
        ("mol/L", "mol/L"),
        ("mmol/L", "mmol/L"),
        ("μmol/L", "μmol/L"),
        ("nmol/L", "nmol/L"),
        ("pmol/L", "pmol/L"),
    ]

    VOLUME_CHOICES = [
        ("L", "L"),
        ("mL", "mL"),
        ("μL", "μL"),
        ("nL", "nL"),
        ("pL", "pL"),
    ]

    solute = forms.CharField(
        max_length=100,
        required=False,
        help_text=(
            "Optional: Enter the chemical formula of the solute."
        ),
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
        help_text=(
            "Enter the concentration of the initial solute in the solution "
            "and choose the corresponding unit prefix."
        ),
    )

    v1 = forms.FloatField(
        min_value=0,
        label="Initial volume of the solute.",
        required=False,
        help_text=(
            "Enter the volume of the initial solution and choose the "
            "corresponding volume unit prefix."
        ),
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
        help_text=(
            "Choose a concentration unit for the initial concentration of "
            "the solute."
        ),
    )
    c2_unit = forms.ChoiceField(
        required=False,
        choices=CONCENTRATION_CHOICES,
        label="Final concentration scale unit.",
        initial="mol/L",
        help_text=(
            "Choose a concentration unit for the final concentration of the "
            "solute."
        ),
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

    def clean(self):
        """Validate dilution form fields."""
        cleaned_data = super().clean()
        c1 = cleaned_data.get("c1")
        v1 = cleaned_data.get("v1")
        c2 = cleaned_data.get("c2")
        v2 = cleaned_data.get("v2")

        values = [c1, v1, c2, v2]
        if sum(v is not None for v in values) != 3:
            raise forms.ValidationError(
                "Exactly three values among Initial Concentration, "
                "Initial Volume, Final Concentration, and Final Volume "
                "must be provided."
            )

        labels = [
            "Initial Concentration",
            "Initial Volume",
            "Final Concentration",
            "Final Volume",
        ]
        for value, label in zip(values, labels):
            if value is not None and value <= 0:
                raise forms.ValidationError(
                    f"The {label.lower()} cannot be equal or lower than zero."
                )

        from .utils.units import Q_

        if v1 is not None and v2 is not None:
            v1_q = Q_(v1, cleaned_data.get("v1_unit"))
            v2_q = Q_(v2, cleaned_data.get("v2_unit"))
            if v1_q.to("liter").magnitude >= v2_q.to("liter").magnitude:
                raise forms.ValidationError(
                    "Initial volume cannot be greater or equal than final volume."
                )

        if c1 is not None and c2 is not None:
            c1_q = Q_(c1, cleaned_data.get("c1_unit"))
            c2_q = Q_(c2, cleaned_data.get("c2_unit"))
            if c1_q.to("mol/liter").magnitude < c2_q.to("mol/liter").magnitude:
                raise forms.ValidationError(
                    "Initial concentration cannot be lesser than final concentration."
                )

        return cleaned_data
