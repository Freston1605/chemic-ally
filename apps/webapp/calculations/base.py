"""Calculation helpers for ChemicAlly."""

# Each calculation type implements a ``calculate`` method with unified handling
# of chempy objects.

from typing import Any, Dict, Optional, Tuple, Union

from chempy import Substance, balance_stoichiometry
from ..utils.units import Q_


# --- Calculation Base ---
class CalculationBase:
    """
    Base class for chemical calculations. Defines input/output and
    documentation metadata.
    Subclasses must implement the `calculate` method.
    """

    description: str = "Base calculation class."
    input_spec: Dict[str, Any] = {}
    output_spec: Dict[str, Any] = {}

    def calculate(self, *args, **kwargs):
        raise NotImplementedError("Subclasses must implement this method.")


# --- Molecular Weight Calculator ---
class MolecularWeightCalculator(CalculationBase):
    description = (
        "Calculates the molecular weight of a chemical compound from its formula."
    )
    input_spec = {"formula": str}
    output_spec = {"molecular_weight": float}

    def calculate(
        self, substance_or_formula: Union[str, Substance]
    ) -> Optional[float]:
        """
        Calculates the molar mass of a chemical compound given its formula or Substance.
        Args:
            substance_or_formula: Formula string or ``chempy.Substance`` instance.
        Returns:
            float or None: The molar mass if valid, else None.
        """
        try:
            if isinstance(substance_or_formula, Substance):
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

    def calculate(
        self, reactants: list, products: list
    ) -> Optional[Tuple[Dict[str, int], Dict[str, int]]]:
        try:
            reactants_balanced, products_balanced = balance_stoichiometry(
                reactants=reactants, products=products
            )
            return reactants_balanced, products_balanced
        except Exception:
            # Log or handle error more gracefully if needed
            return None

    @staticmethod
    def to_latex(
        reactants: Dict[str, int], products: Dict[str, int], reversible: bool = True
    ) -> str:
        reactants_str = " + ".join(
            [f"{v}{k}" if v != 1 else k for k, v in reactants.items()]
        )
        products_str = " + ".join(
            [f"{v}{k}" if v != 1 else k for k, v in products.items()]
        )
        reaction_arrow = "\\rightleftharpoons" if reversible else "\\rightarrow"
        latex_reaction = (
            f"$$ \\ce{{{reactants_str} {reaction_arrow} {products_str}}} $$"
        )
        return latex_reaction


# --- Dilution Calculator ---
class DilutionCalculator(CalculationBase):
    description = (
        "Performs dilution calculations (C1V1 = C2V2). "
        "Given three values, computes the fourth. Unit-safe with Pint."
    )
    input_spec = {
        "c1": float,
        "c1_unit": str,
        "v1": float,
        "v1_unit": str,
        "c2": float,
        "c2_unit": str,
        "v2": float,
        "v2_unit": str,
        "molecular_weight": float,
        "molecular_weight_unit": str,
        "solute_formula": str,
    }
    output_spec = {"missing_property": str, "missing_value": object, "mass_g": float}

    def calculate(
        self,
        c1: Optional[float],
        c1_unit: str,
        v1: Optional[float],
        v1_unit: str,
        c2: Optional[float],
        c2_unit: str,
        v2: Optional[float],
        v2_unit: str,
        molecular_weight: Optional[float] = None,
        molecular_weight_unit: str = "g/mol",
        solute_formula: Optional[str] = None,
    ) -> dict:
        """
        Calculate the missing value in a dilution. Mass is computed if
        possible, all handled safely with Pint. All inputs must provide both
        value and unit string (e.g., ``10`` and ``"mmol/L"``).
        """
        values = {
            "c1": (c1, c1_unit),
            "v1": (v1, v1_unit),
            "c2": (c2, c2_unit),
            "v2": (v2, v2_unit),
        }
        provided = {
            k: Q_(v, u) for k, (v, u) in values.items() if v is not None
        }
        missing_key = [k for k in values if values[k][0] is None]
        if len(missing_key) != 1:
            raise ValueError("Exactly one value among c1, v1, c2, v2 must be missing")
        miss = missing_key[0]

        # Calculate the missing value using Pint (units handled automatically)
        if miss == "c1":
            c1q = (provided["c2"] * provided["v2"]) / provided["v1"]
            missing = c1q
        elif miss == "v1":
            v1q = (provided["c2"] * provided["v2"]) / provided["c1"]
            missing = v1q
        elif miss == "c2":
            c2q = (provided["c1"] * provided["v1"]) / provided["v2"]
            missing = c2q
        elif miss == "v2":
            v2q = (provided["c1"] * provided["v1"]) / provided["c2"]
            missing = v2q

        result = {"missing_property": miss, "missing_value": missing.to_compact()}

        # Mass calculation if possible
        # Derive molecular weight from formula if not provided
        mw = molecular_weight
        if not mw and solute_formula:
            try:
                mw = Substance.from_formula(solute_formula).mass
            except Exception:
                mw = None
        if mw and all(x in provided for x in ["c2", "v2"]):
            # Convert c2 to mol/L, v2 to L
            c2_mol_L = provided["c2"].to("mol/l")
            v2_L = provided["v2"].to("l")
            moles = c2_mol_L.magnitude * v2_L.magnitude
            mass = moles * mw  # g
            result["mass_g"] = mass

        return result


# --- (Optional) Registry for Dynamic Discovery ---
CALCULATION_REGISTRY = {
    "molecular_weight": MolecularWeightCalculator,
    "reaction_balancer": ReactionBalancer,
    "dilution": DilutionCalculator,
    # Add more calculators here
}
