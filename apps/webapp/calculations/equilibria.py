"""Chemical equilibria calculator using chempy's EqSystem."""

import logging
from collections import defaultdict
from math import log10
from typing import Any, Dict, List

from chempy.equilibria import EqSystem

from .base import CalculationBase

logger = logging.getLogger(__name__)


class EquilibriaCalculator(CalculationBase):
    """
    Solves a system of coupled chemical equilibria in aqueous solution.

    Uses ``chempy.equilibria.EqSystem`` to compute the equilibrium
    composition of a multi-reaction system given a set of reactions with
    equilibrium constants and initial concentrations.

    Input:
        - ``equations``: list of strings, each in the format
          ``"HCO3- = H+ + CO3-2; 10**-10.3"``
        - ``concentrations``: dict mapping substance name → initial
          concentration in mol/L
        - ``solvent``: solvent substance name (default ``"H2O"``)
        - ``solvent_concentration``: solvent concentration in mol/L
          (default ``55.4``)

    Output:
        - ``ph``: float or None (if no H+ in species list)
        - ``species``: dict mapping substance → equilibrium concentration
        - ``sane``: bool from EqSystem
        - ``info``: dict with solver info
        - ``success``: bool
    """

    description = (
        "Solves coupled chemical equilibria (acid-base, complexation, etc.) "
        "in aqueous solution using chempy's EqSystem."
    )
    input_spec = {
        "equations": list,
        "concentrations": dict,
        "solvent": str,
        "solvent_concentration": float,
    }
    output_spec = {
        "ph": float,
        "species": dict,
        "sane": bool,
        "info": dict,
        "success": bool,
    }

    def calculate(
        self,
        equations: List[str],
        concentrations: Dict[str, float],
        solvent: str = "H2O",
        solvent_concentration: float = 55.4,
    ) -> Dict[str, Any]:
        """
        Compute equilibrium composition for the given system.

        Args:
            equations:
                List of reaction strings, e.g.
                ``["HCO3- = H+ + CO3-2; 10**-10.3",
                    "H2CO3 = H+ + HCO3-; 10**-6.3"]``
            concentrations:
                Initial concentrations in mol/L, e.g.
                ``{"H2O": 55.4, "HCO3-": 1e-2}``
            solvent:
                Name of the solvent substance (default ``"H2O"``).
            solvent_concentration:
                Concentration of the solvent in mol/L (default 55.4).

        Returns:
            dict with keys:

            - ``ph``: pH of the solution (``-log10[H+]``) or ``None``
            - ``species``: equilibrium concentrations of all species
            - ``sane``: convergence sanity flag from chempy
            - ``info``: additional solver information (iterations, etc.)
            - ``success``: whether the calculation succeeded
            - ``error``: error message if failed (only present on failure)
        """
        try:
            # Build the multi-line string for EqSystem.from_string
            reaction_string = "\n".join(equations)

            # Build the initial concentrations dict
            # Ensure solvent is included
            init_conc = defaultdict(float, concentrations)
            if solvent not in init_conc or init_conc[solvent] == 0.0:
                init_conc[solvent] = solvent_concentration

            # Create the equilibrium system
            eqsys = EqSystem.from_string(reaction_string)
            substances = eqsys.substances

            # Solve
            arr, info, sane = eqsys.root(init_conc)

            # Map results to substance names
            equilibrium_concentrations = dict(zip(substances, arr))

            # Calculate pH if H+ is present
            ph = None
            if "H+" in equilibrium_concentrations:
                h_conc = equilibrium_concentrations["H+"]
                if h_conc > 0:
                    ph = -log10(h_conc)

            return {
                "ph": ph,
                "species": equilibrium_concentrations,
                "sane": bool(sane),
                "info": {
                    "iterations": info.get("iterations", 0),
                    "funcalls": info.get("funcalls", 0),
                },
                "success": True,
            }

        except Exception as e:
            logger.exception("Equilibria calculation failed")
            return {
                "success": False,
                "error": str(e),
                "ph": None,
                "species": {},
                "sane": False,
                "info": {},
            }
