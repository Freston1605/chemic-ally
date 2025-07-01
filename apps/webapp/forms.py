from django import forms
from chempy import Substance
import re
from .utils import smiles_to_formula


class FormulaListField(forms.CharField):
    """Return a list of formulas split on common separators."""

    default_separators = r"[\s,;]+"

    def __init__(self, *args, validate_formula=True, separators=None, **kwargs):
        self.validate_formula = validate_formula
        self.separators = separators or self.default_separators
        super().__init__(*args, **kwargs)

    def parse_list(self, value: str) -> list[str]:
        tokens = re.split(self.separators, value)
        parts: list[str] = []
        for token in tokens:
            token = token.strip()
            if not token or token == "+":
                continue
            subparts = re.split(r"(?<=\w)\+(?=\w)", token)
            for part in subparts:
                part = part.strip()
                if part:
                    parts.append(part)
        return parts

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return []
        return self.parse_list(value)

    def validate(self, value):
        super().validate(value)
        if self.validate_formula:
            cleaned = []
            for formula in value:
                try:
                    if (
                        re.search(r"[+-]\d*$", formula)
                        or re.search(r"[A-Z][a-z]", formula)
                        or (formula.isalpha() and len(formula) <= 2 and any(ch != "C" for ch in formula))
                    ):
                        Substance.from_formula(formula)
                        cleaned.append(formula)
                        continue
                except Exception:
                    pass
                try:
                    cleaned.append(smiles_to_formula(formula))
                    continue
                except Exception:
                    pass
                try:
                    Substance.from_formula(formula)
                    cleaned.append(formula)
                except Exception:
                    raise forms.ValidationError(
                        f"Invalid formula or SMILES: {formula}",
                        code="invalid_formula",
                    )
            value[:] = cleaned

    def clean(self, value):
        value = self.to_python(value)
        self.validate(value)
        return value


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

    formula = FormulaListField(
        max_length=100,
        label="Chemical Formula",
        help_text=(
            "Enter the chemical formula or SMILES string of all substances "
            "separated by spaces, commas or plus signs."
        ),
        widget=forms.TextInput(attrs={"placeholder": "NH4+ CO2 H2O"}),
        required=True,
    )


class ChemicalReactionForm(forms.Form):
    """
    A Django form for capturing a molecular reaction.
    Products and reactants can be separated by spaces, commas or plus signs.
    Alternatively, an entire reaction may be entered using an equal sign to
    separate reactants from products.

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

    reactant = FormulaListField(
        max_length=100,
        label="Reactant(s)",
        help_text=(
            "Enter formulas or SMILES for all reactants separated "
            "by spaces, commas or plus signs."
        ),
        widget=forms.TextInput(attrs={"placeholder": "H2 O2"}),
    )
    product = FormulaListField(
        max_length=100,
        label="Product(s)",
        help_text=(
            "Enter formulas or SMILES for all reactants separated "
            "by spaces, commas or plus signs."
        ),
        widget=forms.TextInput(attrs={"placeholder": "H2O"}),
    )
    reversible = forms.BooleanField(
        initial=True,
        required=False,
        label="Irreversible Reaction",
        help_text="Uncheck this box if the reaction is not reversible",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.data:
            data = self.data.copy()
            reactant_raw = data.get("reactant", "")
            product_raw = data.get("product", "")
            if "=" in reactant_raw and not product_raw:
                left, right = reactant_raw.split("=", 1)
                data["reactant"] = left
                data["product"] = right
                self.data = data
            elif "=" in product_raw and not reactant_raw:
                left, right = product_raw.split("=", 1)
                data["reactant"] = left
                data["product"] = right
                self.data = data

    def clean(self):
        """
        Cleans and parses the input data to obtain reactants and products separately.

        Raises:
            forms.ValidationError: Raised if neither reactant nor product is provided.

        Returns:
            dict: A dictionary containing the cleaned and parsed data.
        """
        cleaned_data = super().clean()
        if not cleaned_data.get("reactant") or not cleaned_data.get("product"):
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

    solute = FormulaListField(
        max_length=100,
        required=False,
        help_text=(
            "Optional: Enter the solute formula or SMILES separated "
            "by spaces, commas or plus signs."
        ),
        widget=forms.TextInput(attrs={"placeholder": "NaOH"}),
    )
    solvent = FormulaListField(
        max_length=100,
        required=False,
        help_text=(
            "Optional: Enter the solvent formula or SMILES separated "
            "by spaces, commas or plus signs."
        ),
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
