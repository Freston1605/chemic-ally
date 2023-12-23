from chempy import Substance, balance_stoichiometry
from pyparsing import ParseException


def calculate_molecular_weight(formula):
    """
    Calculate the molecular weight of a chemical compound.

    This function uses the chempy.Substance.from_formula to calculate the molecular weight of a molecule.

    Parameters:
    - formula (str): The chemical formula of the compound.

    Returns:
    - float: The calculated molecular weight of the compound.

    Example:
    >>> calculate_weight('H2O')
    18.01528
    """
    # Use ChemPy to calculate molecular weight
    try:
        # Attempt to parse the formula
        compound = Substance.from_formula(formula)
        molecular_weight = compound.mass
        return molecular_weight
    except ParseException as e:
        # Handle parsing errors
        print(f"Error parsing the formula: {e}")
        return None


def balance_chemical_reaction(reactants, products):
    """
    Balance the stoichiometry coefficients of chemical reactants and products in a chemical reaction.

    This function uses the chempy.balance_stoichiometry method to calculate and return the balanced
    stoichiometry coefficients for all reactants and products in a chemical reaction.

    Args:
        reactants (dict): Iterable dictionary with keys representing the chemical formulas of reactants as strings.
        products (dict): Iterable dictionary with keys representing the chemical formulas of products as strings.

    Returns:
        tuple: A tuple containing two dictionaries representing the balanced stoichiometry coefficients.
               The first dictionary corresponds to the balanced reactants, and the second to the balanced products.
               The dictionaries have chemical formulas as keys and stoichiometric coefficients as values.

    Example:
        Given reactants {'H2', 'O2'} and products {'H2O'},
        the function may return ({'H2': 2, 'O2': 1}, {'H2O': 2}) representing the balanced coefficients.
    """
    try:
        # Calculate the stoichiometric coefficients
        reactants_balanced, products_balanced = balance_stoichiometry(
            reactants=reactants, products=products
        )
        return reactants_balanced, products_balanced
    except Exception as e:
        # Handle the specific error and provide a more user-friendly message
        raise ValueError(
            "Please check your reactants and products for any errors."
        ) from e


def balance_chemical_reaction_to_latex(reactants, products, reversible=True):
    """
    Convert a balanced chemical reaction represented as dictionaries to LaTeX format.

    Args:
        reactants (dict): Dictionary containing chemical formulas as keys and stoichiometric coefficients as values
                          for the reactants in a chemical reaction.
        products (dict): Dictionary containing chemical formulas as keys and stoichiometric coefficients as values
                         for the products in a chemical reaction.
        reversible (bool): Whether the reaction is reversible. Default is True.

    Returns:
        str: A string representation of the balanced chemical reaction in LaTeX format.

    Example:
        Given reactants {'H2': 2, 'O2': 1} and products {'H2O': 2},
        the function may return "\\ce{2H2 + O2 <=> 2H2O}" representing the balanced reaction.
    """
    reactants_str = " + ".join(
        [f"{v}{k}" if v != 1 else k for k, v in reactants.items()]
    )
    products_str = " + ".join([f"{v}{k}" if v != 1 else k for k, v in products.items()])

    reaction_arrow = "\\rightleftharpoons" if reversible else "\\rightarrow"

    latex_reaction = f"$$ \\ce{{{reactants_str} {reaction_arrow} {products_str}}} $$"

    return latex_reaction
