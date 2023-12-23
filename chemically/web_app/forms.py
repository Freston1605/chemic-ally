from django import forms


class MolecularFormulaForm(forms.Form):
    """
    A Django form for capturing a molecular formula.

    Parameters:
    - formula (CharField): A character field for the molecular formula.
                          The maximum length is set to 100 characters.

    Example usage:
    >>> form = MoleculeFormulaForm(data={'formula': 'H2O'})
    >>> form.is_valid()
    True
    >>> form.cleaned_data
    {'formula': 'H2O'}
    """

    formula = forms.CharField(max_length=100)


class ChemicalReaction(forms.Form):
    """
    A Django form for capturing a molecular reaction.
    Products and reactants are meant to be separated with a plus sign '+'.

    Attributes:
        reactant (CharField): A field for capturing the reactants in the chemical reaction.
                             Max length is set to 100 characters.
                             Example: 'H2 + O2'.

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
        help_text="Enter the chemical formula for all of the reactants in this input box separated by a plus sign '+'.",
        widget=forms.TextInput(attrs={"placeholder": "H2 + O2"}),
    )
    product = forms.CharField(
        max_length=100,
        label="Product(s)",
        help_text="Enter the chemical formula for all of the reactants in this input box separated by a plus sign '+'.",
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