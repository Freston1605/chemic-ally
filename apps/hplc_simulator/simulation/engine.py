"""
Chromatographic simulation engine for HPLC.

Implements the Linear Solvent Strength (LSS) model for retention time prediction,
gradient elution calculations, peak shape modeling, and system pressure estimation.

References:
- Snyder, L.R., Dolan, J.W. "High-Performance Gradient Elution" (2007)
- Neue, U.D. "HPLC Columns: Theory, Technology, and Practice" (1997)
"""

import math
import numpy as np
from scipy.special import erf
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class AnalyteProperties:
    """Properties of an analyte affecting chromatographic behavior."""
    name: str
    log_kw: float
    s_parameter: float
    pka: Optional[float] = None
    neutral_charge: bool = True


@dataclass
class ColumnConfig:
    """Column (stationary phase) configuration."""
    chemistry: str
    length_mm: float
    id_mm: float
    particle_size_um: float


@dataclass
class MobilePhaseConfig:
    """Mobile phase configuration."""
    start_b: float = 5.0
    end_b: float = 95.0
    ramp_time: float = 20.0
    ph: float = 3.0
    buffer_concentration_mm: float = 10.0
    is_gradient: bool = True


@dataclass
class OperationConfig:
    """Operational settings."""
    flow_rate_ml_min: float = 1.0
    temperature_c: float = 30.0
    injection_volume_ul: float = 10.0


@dataclass
class PeakInfo:
    """Information about a detected peak in the chromatogram."""
    analyte: str
    retention_time: float
    width: float
    height: float
    area: float


@dataclass
class SimulationResult:
    """Complete result from a chromatographic simulation."""
    time: np.ndarray
    signal: np.ndarray
    peaks: List[PeakInfo]
    total_run_time: float
    max_pressure_bar: float
    min_resolution: float
    critical_pair: Optional[Tuple[str, str]] = None
    overpressure: bool = False


def calculate_retention_factor_isocratic(
    log_kw: float,
    s_parameter: float,
    phi: float,
    pka: Optional[float] = None,
    ph: float = 3.0,
    neutral_charge: bool = True,
) -> float:
    """
    Calculate retention factor k using the LSS model.

    log k = log k_w - S * phi

    With pH correction for ionizable analytes using a simplified model:
    k_observed = k_neutral / (1 + 10^(pH - pKa)) for acids
    k_observed = k_neutral / (1 + 10^(pKa - pH)) for bases

    Args:
        log_kw: Log of retention factor in pure water
        s_parameter: LSS S parameter
        phi: Volume fraction of organic modifier (0-1)
        pka: Acid dissociation constant (None for neutral compounds)
        ph: Mobile phase pH
        neutral_charge: Whether the compound is neutral

    Returns:
        Retention factor k
    """
    phi = max(0.0, min(1.0, phi))

    log_k = log_kw - s_parameter * phi
    k_neutral = 10 ** log_k

    if pka is not None and not neutral_charge:
        ionization_factor = _calculate_ionization_factor(pka, ph)
        k_neutral *= ionization_factor

    return max(0.01, k_neutral)


def _calculate_ionization_factor(pka: float, ph: float) -> float:
    """
    Calculate ionization factor for ionizable analytes.

    Returns a factor between 0 and 1 representing the fraction of analyte
    in the retained (neutral) form.
    """
    delta = ph - pka
    if delta > 3:
        return 0.01
    elif delta < -3:
        return 1.0
    else:
        return 1.0 / (1.0 + 10 ** delta)


def calculate_effective_retention_gradient(
    analyte: AnalyteProperties,
    column: ColumnConfig,
    mobile: MobilePhaseConfig,
    operation: OperationConfig,
) -> float:
    """
    Calculate effective retention time for gradient elution.

    Uses the fundamental gradient elution equation:
    t_R = t_0 + (t_G / b) * ln(1 + b * k_0)

    where b = (S * delta_phi * t_0) / t_G

    Args:
        analyte: Analyte properties
        column: Column configuration
        mobile: Mobile phase configuration
        operation: Operational settings

    Returns:
        Retention time in minutes
    """
    t_0 = _calculate_dead_time(column, operation)

    if not mobile.is_gradient:
        k = calculate_retention_factor_isocratic(
            analyte.log_kw,
            analyte.s_parameter,
            mobile.start_b / 100.0,
            analyte.pka,
            mobile.ph,
            analyte.neutral_charge,
        )
        return t_0 * (1 + k)

    delta_phi = (mobile.end_b - mobile.start_b) / 100.0
    t_g = mobile.ramp_time

    k_initial = calculate_retention_factor_isocratic(
        analyte.log_kw,
        analyte.s_parameter,
        mobile.start_b / 100.0,
        analyte.pka,
        mobile.ph,
        analyte.neutral_charge,
    )

    b = (analyte.s_parameter * delta_phi * t_0) / t_g

    if b < 0.001:
        k = calculate_retention_factor_isocratic(
            analyte.log_kw,
            analyte.s_parameter,
            mobile.start_b / 100.0,
            analyte.pka,
            mobile.ph,
            analyte.neutral_charge,
        )
        return t_0 * (1 + k)

    t_r = t_0 + (t_g / b) * math.log(1 + b * k_initial)

    dwell_time = 1.0
    t_r += dwell_time

    return max(t_0, t_r)


def _calculate_dead_time(column: ColumnConfig, operation: OperationConfig) -> float:
    """
    Calculate column dead time (t_0) in minutes.

    t_0 = (pi * r^2 * L * epsilon) / F

    where epsilon is column porosity (~0.7 for packed columns)
    """
    radius_cm = (column.id_mm / 2) / 10.0
    length_cm = column.length_mm / 10.0
    porosity = 0.7
    volume_ml = math.pi * (radius_cm ** 2) * length_cm * porosity
    flow_rate_ml_min = operation.flow_rate_ml_min

    if flow_rate_ml_min <= 0:
        flow_rate_ml_min = 0.1

    return volume_ml / flow_rate_ml_min


def calculate_column_pressure(
    column: ColumnConfig,
    operation: OperationConfig,
) -> float:
    """
    Calculate system backpressure using an empirical HPLC pressure model.

    Based on the Kozeny-Carman equation calibrated to typical HPLC performance.
    Reference: 150 x 4.6 mm, 5 um column at 1 mL/min, 30C ~ 120 bar.

    Args:
        column: Column configuration
        operation: Operational settings

    Returns:
        Pressure in bar
    """
    viscosity = _calculate_viscosity(operation.temperature_c)
    reference_viscosity = _calculate_viscosity(30.0)

    length_factor = column.length_mm / 150.0
    flow_factor = operation.flow_rate_ml_min / 1.0
    particle_factor = (5.0 / column.particle_size_um) ** 2
    id_factor = (4.6 / column.id_mm) ** 2
    viscosity_factor = viscosity / reference_viscosity

    reference_pressure = 120.0

    pressure = (
        reference_pressure
        * length_factor
        * flow_factor
        * particle_factor
        * id_factor
        * viscosity_factor
    )

    return max(0.0, pressure)


def _calculate_viscosity(temperature_c: float) -> float:
    """
    Calculate mobile phase viscosity at given temperature.

    Uses empirical correlation for water/acetonitrile mixtures.
    Viscosity decreases with increasing temperature.

    Returns:
        Viscosity in Pa*s
    """
    temp_k = temperature_c + 273.15
    reference_viscosity = 0.001
    viscosity = reference_viscosity * math.exp(1800 * (1.0 / temp_k - 1.0 / 298.15))

    return viscosity


def calculate_peak_width(
    retention_time: float,
    column: ColumnConfig,
    operation: OperationConfig,
    injection_volume_ul: float = 10.0,
) -> float:
    """
    Calculate peak width at baseline (4 sigma).

    Uses plate count N and accounts for extra-column band broadening.

    w = 4 * sigma = 4 * t_R / sqrt(N)

    N = L / H, where H is plate height from van Deemter equation
    """
    t_0 = _calculate_dead_time(column, operation)
    k = (retention_time / t_0) - 1 if t_0 > 0 else 1

    n_theoretical = _calculate_plate_count(column, operation, k)

    sigma_time = retention_time / math.sqrt(n_theoretical)

    injection_contribution = (injection_volume_ul / 1000.0) / operation.flow_rate_ml_min
    extra_column_sigma = injection_contribution / 4.0

    total_sigma = math.sqrt(sigma_time ** 2 + extra_column_sigma ** 2)

    return 4.0 * total_sigma


def _calculate_plate_count(
    column: ColumnConfig,
    operation: OperationConfig,
    k: float = 1.0,
) -> float:
    """
    Calculate theoretical plate count using van Deemter equation.

    H = A + B/u + C*u

    where u is linear velocity, and A, B, C are van Deemter coefficients.
    """
    radius_m = (column.id_mm / 2) / 1000.0
    flow_rate_m3_s = (operation.flow_rate_ml_min / 1000.0) / 60.0
    cross_section = math.pi * (radius_m ** 2)
    linear_velocity = flow_rate_m3_s / cross_section

    dp = column.particle_size_um

    a_term = 1.5 * dp * 1e-6
    b_term = 2.0 * 1e-9
    c_term = 0.05 * (dp * 1e-6) ** 2 / (1e-9)

    if linear_velocity > 0:
        h = a_term + (b_term / linear_velocity) + (c_term * linear_velocity)
    else:
        h = a_term + c_term * 0.001

    length_m = column.length_mm / 1000.0
    n = length_m / h if h > 0 else 10000

    return max(500.0, min(n, 200000.0))


def generate_chromatogram(
    analytes: List[AnalyteProperties],
    column: ColumnConfig,
    mobile: MobilePhaseConfig,
    operation: OperationConfig,
    max_pressure_bar: float = 400.0,
    time_points: int = 5000,
    noise_level: float = 0.5,
) -> SimulationResult:
    """
    Generate a complete chromatogram for the given parameters.

    Args:
        analytes: List of analytes to simulate
        column: Column configuration
        mobile: Mobile phase configuration
        operation: Operational settings
        max_pressure_bar: Maximum allowable system pressure
        time_points: Number of data points in the chromatogram
        noise_level: Baseline noise amplitude (mAU)

    Returns:
        SimulationResult with chromatogram data and metrics
    """
    pressure = calculate_column_pressure(column, operation)
    overpressure = pressure > max_pressure_bar

    if overpressure:
        return SimulationResult(
            time=np.array([0.0]),
            signal=np.array([0.0]),
            peaks=[],
            total_run_time=0.0,
            max_pressure_bar=pressure,
            min_resolution=0.0,
            overpressure=True,
        )

    total_run_time = mobile.ramp_time + 5.0 if mobile.is_gradient else 30.0
    time_array = np.linspace(0, total_run_time, time_points)
    signal = np.zeros(time_points)

    peaks = []

    for analyte in analytes:
        t_r = calculate_effective_retention_gradient(analyte, column, mobile, operation)

        if t_r > total_run_time:
            continue

        width = calculate_peak_width(
            t_r, column, operation, operation.injection_volume_ul,
        )

        peak_signal = _generate_peak(time_array, t_r, width, height=1000.0)
        signal += peak_signal

        area = float(np.trapezoid(peak_signal, time_array))

        peaks.append(PeakInfo(
            analyte=analyte.name,
            retention_time=t_r,
            width=width,
            height=float(np.max(peak_signal)),
            area=area,
        ))

    noise = np.random.normal(0, noise_level, time_points)
    signal += noise
    signal = np.maximum(signal, 0)

    min_resolution, critical_pair = calculate_resolution(peaks)

    return SimulationResult(
        time=time_array,
        signal=signal,
        peaks=peaks,
        total_run_time=total_run_time,
        max_pressure_bar=pressure,
        min_resolution=min_resolution,
        critical_pair=critical_pair,
        overpressure=False,
    )


def _generate_peak(
    time_array: np.ndarray,
    retention_time: float,
    width: float,
    height: float = 1000.0,
    tailing_factor: float = 1.2,
) -> np.ndarray:
    """
    Generate a peak with Gaussian shape and optional tailing.

    Uses an exponentially modified Gaussian (EMG) model for realistic peak shape.
    """
    sigma = width / 4.0

    if sigma <= 0:
        return np.zeros_like(time_array)

    tau = sigma * (tailing_factor - 1.0)

    if tau <= 0:
        tau = sigma * 0.1

    exp_arg = (sigma ** 2) / (2 * tau ** 2) - (time_array - retention_time) / tau
    exp_arg = np.clip(exp_arg, -100, 100)

    erf_arg = (
        (time_array - retention_time) / (sigma * math.sqrt(2))
        - sigma / (tau * math.sqrt(2))
    )
    erf_arg = np.clip(erf_arg, -10, 10)

    peak = np.where(
        np.abs(time_array - retention_time) < 4 * sigma,
        (height / 2) * np.exp(exp_arg) * (1 + erf(erf_arg)),
        0
    )

    return peak


def calculate_resolution(
    peaks: List[PeakInfo],
) -> Tuple[float, Optional[Tuple[str, str]]]:
    """
    Calculate minimum resolution between adjacent peaks.

    Rs = 2 * (tR2 - tR1) / (w1 + w2)

    Args:
        peaks: List of PeakInfo objects

    Returns:
        Tuple of (minimum resolution, critical pair names)
    """
    if len(peaks) < 2:
        return 0.0, None

    sorted_peaks = sorted(peaks, key=lambda p: p.retention_time)

    min_rs = float('inf')
    critical_pair = None

    for i in range(len(sorted_peaks) - 1):
        p1 = sorted_peaks[i]
        p2 = sorted_peaks[i + 1]

        delta_tr = p2.retention_time - p1.retention_time
        w_sum = p1.width + p2.width

        if w_sum > 0:
            rs = (2 * delta_tr) / w_sum
        else:
            rs = 0.0

        if rs < min_rs:
            min_rs = rs
            critical_pair = (p1.analyte, p2.analyte)

    return min_rs, critical_pair


def calculate_score(
    total_run_time: float,
    min_resolution: float,
    max_pressure_bar: float,
    pressure_limit: float = 400.0,
    base_points: float = 10000.0,
    min_resolution_target: float = 1.5,
) -> Tuple[float, bool]:
    """
    Calculate score for a chromatographic run.

    Score = (BasePoints / t_total) - PressurePenalty + RsBonus

    Args:
        total_run_time: Total run time in minutes
        min_resolution: Minimum resolution between adjacent peaks
        max_pressure_bar: Maximum pressure during run
        pressure_limit: Maximum allowable pressure
        base_points: Base points for scoring
        min_resolution_target: Target resolution for success

    Returns:
        Tuple of (score, is_successful)
    """
    if total_run_time <= 0:
        return 0.0, False

    base_score = base_points / total_run_time

    pressure_ratio = max_pressure_bar / pressure_limit if pressure_limit > 0 else 1.0
    pressure_penalty = 0.0

    if pressure_ratio > 0.8:
        pressure_penalty = (pressure_ratio - 0.8) * base_points * 0.5

    rs_bonus = 0.0
    is_successful = min_resolution >= min_resolution_target

    if is_successful:
        rs_bonus = (min_resolution - min_resolution_target) * base_points * 0.1

    score = base_score - pressure_penalty + rs_bonus

    return max(0.0, score), is_successful
