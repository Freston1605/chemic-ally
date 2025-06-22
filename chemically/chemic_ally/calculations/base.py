"""
Class-based calculation logic for ChemicAlly (refactored).
Each calculation type implements a `calculate` method, with unified handling of chempy objects.
"""

from chempy import Substance, balance_stoichiometry
from pyparsing import ParseException
from typing import Any, Dict, Optional, Tuple, Union

# --- Substance Wrapper ---
class SubstanceWrapper:
    """
    Wraps a chempy Substance for reusability across multiple calculations.
    """
    def __init__(self, formula: str):
        try:
            self.substance = Substance.from_formula(formula)
            self.error = None
        except Exception as e:
            self.substance = None
            self.error = str(e)
        self.formula = formula

    @property
    def mass(self) -> Optional[float]:
        return getattr(self.substance, 'mass', None)

    @property
    def composition(self) -> Optional[Dict[str, float]]:
        return getattr(self.substance, 'composition', None)

    # More properties/methods (e.g., as_dict, elements, etc.) can be added here.

# --- Calculation Base ---
class CalculationBase:
    """
    Base class for chemical calculations. Defines input/output and documentation metadata.
    Subclasses must implement the `calculate` method.
    """
    description: str = "Base calculation class."
    input_spec: Dict[str, Any] = {}
    output_spec: Dict[str, Any] = {}

    def calculate(self, *args, **kwargs):
        raise NotImplementedError("Subclasses must implement this method.")

# --- Molecular Weight Calculator ---
class MolecularWeightCalculator(CalculationBase):
    description = "Calculates the molecular weight of a chemical compound from its formula."
    input_spec = {"formula": str}
    output_spec = {"molecular_weight": float}

    def calculate(self, substance_or_formula: Union[str, Substance, SubstanceWrapper]) -> Optional[float]:
        """
        Calculates the molar mass of a chemical compound given its formula or Substance.
        Args:
            substance_or_formula: Formula string, Substance, or SubstanceWrapper.
        Returns:
            float or None: The molar mass if valid, else None.
        """
        try:
            if isinstance(substance_or_formula, SubstanceWrapper):
                if substance_or_formula.error:
                    return None
                substance = substance_or_formula.substance
            elif isinstance(substance_or_formula, Substance):
                substance = substance_or_formula
            else:
                substance = Substance.from_formula(substance_or_formula)
            return substance.mass
        except Exception:
            return None

# --- Reaction Balancer ---
class ReactionBalancer(CalculationBase):
    description = "Balances a chemical reaction given reactant and product formulas."
    input_spec = {"reactants": list, "products": list}
    output_spec = {"reactants_balanced": dict, "products_balanced": dict}

    def calculate(self, reactants: list, products: list) -> Optional[Tuple[Dict[str, int], Dict[str, int]]]:
        try:
            reactants_balanced, products_balanced = balance_stoichiometry(
                reactants=reactants, products=products
            )
            return reactants_balanced, products_balanced
        except Exception as e:
            # Log or handle error more gracefully if needed
            return None

    @staticmethod
    def to_latex(reactants: Dict[str, int], products: Dict[str, int], reversible: bool = True) -> str:
        reactants_str = " + ".join([
            f"{v}{k}" if v != 1 else k for k, v in reactants.items()
        ])
        products_str = " + ".join([
            f"{v}{k}" if v != 1 else k for k, v in products.items()
        ])
        reaction_arrow = "\\rightleftharpoons" if reversible else "\\rightarrow"
        latex_reaction = f"$$ \\ce{{{reactants_str} {reaction_arrow} {products_str}}} $$"
        return latex_reaction

# --- Dilution Calculator ---
class DilutionCalculator(CalculationBase):
    description = "Performs dilution calculations (C1V1 = C2V2). Given three values, computes the fourth."
    input_spec = {"c1": float, "v1": float, "c2": float, "v2": float}
    output_spec = {"missing_value": float}

    def calculate(self, c1: Optional[float]=None, v1: Optional[float]=None, c2: Optional[float]=None, v2: Optional[float]=None) -> Optional[float]:
        provided_values = sum(1 for value in (c1, v1, c2, v2) if value is not None)
        if provided_values != 3:
            return None
        try:
            if c1 is None:
                return v1 * c2 / v2
            elif v1 is None:
                return v2 * c2 / c1
            elif c2 is None:
                return v2 * c1 / v1
            elif v2 is None:
                return v1 * c1 / c2
        except Exception:
            return None
        return None

    @staticmethod
    def convert_volume(value: float, source_unit: str, target_unit: str) -> float:
        conversion_factors = {
            "L_to_mL": 1000.0,
            "L_to_μL": 1e6,
            "mL_to_L": 1 / 1000.0,
            "mL_to_μL": 1000.0,
            "μL_to_L": 1 / 1e6,
            "μL_to_mL": 1 / 1000.0,
        }
        valid_units = {"L", "mL", "μL"}
        if source_unit not in valid_units or target_unit not in valid_units:
            raise ValueError("Invalid source or target unit")
        conversion_key = f"{source_unit}_to_{target_unit}"
        if conversion_key not in conversion_factors:
            raise ValueError("Unsupported unit conversion")
        conversion_factor = conversion_factors[conversion_key]
        return value * conversion_factor

# --- (Optional) Registry for Dynamic Discovery ---
CALCULATION_REGISTRY = {
    "molecular_weight": MolecularWeightCalculator,
    "reaction_balancer": ReactionBalancer,
    "dilution": DilutionCalculator,
    # Add more calculators here
}
