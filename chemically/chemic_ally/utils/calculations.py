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

def calculate_dilution(c1, v1, c2, v2):
    """Calculates the desired concentration (c1) or volume (v1) of an aliquot for a final dilution.

    Args:
        c1 (float, optional): The desired concentration of the aliquot.
        v1 (float, optional): The desired volume of the aliquot.
        c2 (float): The concentration of the final diluted solution.
        v2 (float): The volume of the final diluted solution.

    Raises:
        ValueError: Raised if there is no specified desired concentration or volume.

    Returns:
        float: The calculated desired concentration (c1) or volume (v1) based on the input.
    """
    # Check for invalid input
    if not (c1 and v1):
        raise ValueError("There needs to be either a desired concentration or volume for the solution.")

    # Calculate the desired concentration
    if not c1 and v1:
        c1 = c2 * v2 / v1
        return c1

    # Calculate the desired volume
    if c1 and not v1:
        v1 = c2 / c1 * v2
        return v1

def convert_volume(value, source_unit, target_unit):
    """
    Convert volume from one unit to another.

    Parameters:
    - value (float): The volume value to be converted.
    - source_unit (str): The source unit of volume (e.g., "L", "mL", "μL").
    - target_unit (str): The target unit of volume (e.g., "L", "mL", "μL").

    Returns:
    - float: The converted volume value in the target unit.
    """
    # Define conversion factors
    conversion_factors = {
        "L_to_mL": 1000.0,
        "L_to_μL": 1e6,
        "mL_to_L": 1 / 1000.0,
        "mL_to_μL": 1000.0,
        "μL_to_L": 1 / 1e6,
        "μL_to_mL": 1 / 1000.0,
    }

    # Check if the units are valid
    valid_units = {"L", "mL", "μL"}
    if source_unit not in valid_units or target_unit not in valid_units:
        raise ValueError("Invalid source or target unit")

    # Generate conversion key
    conversion_key = f"{source_unit}_to_{target_unit}"

    # Check if conversion factor is defined
    if conversion_key not in conversion_factors:
        raise ValueError("Unsupported unit conversion")

    # Perform the conversion
    conversion_factor = conversion_factors[conversion_key]
    converted_value = value * conversion_factor

    return converted_value

def multiply_by_unit(self, value, unit):
    """
    Helper method to multiply a value by its corresponding unit.

    Parameters:
    - value (float): The numerical value.
    - unit (float): The unit multiplier.

    Returns:
    float: The result of the multiplication.
    """
    return value * unit