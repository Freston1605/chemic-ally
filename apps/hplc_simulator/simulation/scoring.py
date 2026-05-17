"""
Scoring engine for HPLC simulation challenges.

Evaluates chromatographic runs against resolution and time objectives.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of how a score was calculated."""
    base_score: float
    pressure_penalty: float
    resolution_bonus: float
    final_score: float
    is_successful: bool
    overpressure: bool
    min_resolution: float
    critical_pair: Optional[Tuple[str, str]]
    message: str


def evaluate_run(
    total_run_time: float,
    min_resolution: float,
    max_pressure_bar: float,
    pressure_limit: float = 400.0,
    base_points: float = 10000.0,
    min_resolution_target: float = 1.5,
    critical_pair: Optional[Tuple[str, str]] = None,
) -> ScoreBreakdown:
    """
    Evaluate a chromatographic run and produce a detailed score breakdown.

    Args:
        total_run_time: Total run time in minutes
        min_resolution: Minimum resolution between adjacent peaks
        max_pressure_bar: Maximum pressure during run
        pressure_limit: Maximum allowable system pressure
        base_points: Base points for scoring formula
        min_resolution_target: Target Rs for baseline separation
        critical_pair: Names of analytes forming the critical pair

    Returns:
        ScoreBreakdown with detailed scoring information
    """
    overpressure = max_pressure_bar > pressure_limit

    if overpressure:
        return ScoreBreakdown(
            base_score=0.0,
            pressure_penalty=0.0,
            resolution_bonus=0.0,
            final_score=0.0,
            is_successful=False,
            overpressure=True,
            min_resolution=min_resolution,
            critical_pair=critical_pair,
            message=(
                f"System overpressure! Pressure: {max_pressure_bar:.0f} bar "
                f"(limit: {pressure_limit:.0f} bar)"
            ),
        )

    if total_run_time <= 0:
        return ScoreBreakdown(
            base_score=0.0,
            pressure_penalty=0.0,
            resolution_bonus=0.0,
            final_score=0.0,
            is_successful=False,
            overpressure=False,
            min_resolution=min_resolution,
            critical_pair=critical_pair,
            message="Invalid run time",
        )

    base_score = base_points / total_run_time

    pressure_ratio = max_pressure_bar / pressure_limit if pressure_limit > 0 else 1.0
    pressure_penalty = 0.0

    if pressure_ratio > 0.8:
        pressure_penalty = (pressure_ratio - 0.8) * base_points * 0.5

    is_successful = min_resolution >= min_resolution_target
    resolution_bonus = 0.0

    if is_successful:
        resolution_bonus = (min_resolution - min_resolution_target) * base_points * 0.1

    final_score = base_score - pressure_penalty + resolution_bonus
    final_score = max(0.0, final_score)

    message = _generate_message(
        is_successful, min_resolution, min_resolution_target, pressure_ratio,
    )

    return ScoreBreakdown(
        base_score=base_score,
        pressure_penalty=pressure_penalty,
        resolution_bonus=resolution_bonus,
        final_score=final_score,
        is_successful=is_successful,
        overpressure=False,
        min_resolution=min_resolution,
        critical_pair=critical_pair,
        message=message,
    )


def _generate_message(
    is_successful: bool,
    min_resolution: float,
    target: float,
    pressure_ratio: float,
) -> str:
    """Generate a user-friendly message based on run results."""
    if is_successful:
        if min_resolution >= 2.0:
            return (
                f"Excellent separation! Rs = {min_resolution:.2f} "
                f"(well above baseline)"
            )
        else:
            return f"Baseline resolution achieved! Rs = {min_resolution:.2f}"

    if pressure_ratio > 0.9:
        return f"Warning: Operating near pressure limit ({pressure_ratio*100:.0f}%)"

    return f"Insufficient resolution. Rs = {min_resolution:.2f} (need >= {target})"
