import pytest
from hplc_simulator.simulation.engine import (
    AnalyteProperties,
    ColumnConfig,
    MobilePhaseConfig,
    OperationConfig,
    calculate_retention_factor_isocratic,
    calculate_effective_retention_gradient,
    calculate_column_pressure,
    calculate_peak_width,
    generate_chromatogram,
    calculate_resolution,
    calculate_score,
    PeakInfo,
)
from hplc_simulator.simulation.scoring import evaluate_run


@pytest.fixture
def sample_analytes():
    return [
        AnalyteProperties(
            name='Caffeine',
            log_kw=0.5,
            s_parameter=3.5,
            pka=0.6,
            neutral_charge=False,
        ),
        AnalyteProperties(
            name='Acetaminophen',
            log_kw=1.2,
            s_parameter=4.0,
            pka=9.38,
            neutral_charge=False,
        ),
        AnalyteProperties(
            name='Ibuprofen',
            log_kw=3.5,
            s_parameter=4.5,
            pka=4.91,
            neutral_charge=False,
        ),
    ]


@pytest.fixture
def standard_column():
    return ColumnConfig(
        chemistry='C18',
        length_mm=150,
        id_mm=4.6,
        particle_size_um=5.0,
    )


@pytest.fixture
def standard_mobile():
    return MobilePhaseConfig(
        start_b=10,
        end_b=90,
        ramp_time=15,
        ph=3.0,
        buffer_concentration_mm=10,
        is_gradient=True,
    )


@pytest.fixture
def standard_operation():
    return OperationConfig(
        flow_rate_ml_min=1.0,
        temperature_c=30,
        injection_volume_ul=10,
    )


class TestRetentionFactorIsocratic:
    def test_basic_retention(self):
        k = calculate_retention_factor_isocratic(
            log_kw=2.0,
            s_parameter=4.0,
            phi=0.3,
        )
        assert k > 0
        expected = 10 ** (2.0 - 4.0 * 0.3)
        assert abs(k - expected) < 0.001

    def test_high_organic_reduces_retention(self):
        k_low = calculate_retention_factor_isocratic(
            log_kw=2.0, s_parameter=4.0, phi=0.1
        )
        k_high = calculate_retention_factor_isocratic(
            log_kw=2.0, s_parameter=4.0, phi=0.9
        )
        assert k_low > k_high

    def test_neutral_analyte_no_ph_effect(self):
        k = calculate_retention_factor_isocratic(
            log_kw=2.0,
            s_parameter=4.0,
            phi=0.5,
            pka=4.0,
            ph=3.0,
            neutral_charge=True,
        )
        expected = 10 ** (2.0 - 4.0 * 0.5)
        assert abs(k - expected) < 0.001

    def test_phi_bounds(self):
        k_negative = calculate_retention_factor_isocratic(
            log_kw=2.0, s_parameter=4.0, phi=-0.5
        )
        k_zero = calculate_retention_factor_isocratic(
            log_kw=2.0, s_parameter=4.0, phi=0.0
        )
        assert abs(k_negative - k_zero) < 0.001

        k_over = calculate_retention_factor_isocratic(
            log_kw=2.0, s_parameter=4.0, phi=1.5
        )
        k_one = calculate_retention_factor_isocratic(
            log_kw=2.0, s_parameter=4.0, phi=1.0
        )
        assert abs(k_over - k_one) < 0.001


class TestEffectiveRetentionGradient:
    def test_gradient_elution_returns_positive_time(
        self, sample_analytes, standard_column, standard_mobile, standard_operation
    ):
        t_r = calculate_effective_retention_gradient(
            sample_analytes[0], standard_column, standard_mobile, standard_operation
        )
        assert t_r > 0

    def test_more_retentive_analyte_elutes_later(
        self, sample_analytes, standard_column, standard_mobile, standard_operation
    ):
        t_r_early = calculate_effective_retention_gradient(
            sample_analytes[0], standard_column, standard_mobile, standard_operation
        )
        t_r_late = calculate_effective_retention_gradient(
            sample_analytes[2], standard_column, standard_mobile, standard_operation
        )
        assert t_r_late > t_r_early

    def test_isocratic_mode(self, standard_column, standard_operation):
        mobile = MobilePhaseConfig(
            start_b=30,
            end_b=30,
            ramp_time=20,
            ph=3.0,
            is_gradient=False,
        )
        analyte = AnalyteProperties(
            name='Test',
            log_kw=2.0,
            s_parameter=4.0,
            neutral_charge=True,
        )
        t_r = calculate_effective_retention_gradient(
            analyte, standard_column, mobile, standard_operation
        )
        assert t_r > 0


class TestColumnPressure:
    def test_pressure_increases_with_flow_rate(self, standard_column):
        op_low = OperationConfig(
            flow_rate_ml_min=0.5, temperature_c=30, injection_volume_ul=10,
        )
        op_high = OperationConfig(
            flow_rate_ml_min=2.0, temperature_c=30, injection_volume_ul=10,
        )

        p_low = calculate_column_pressure(standard_column, op_low)
        p_high = calculate_column_pressure(standard_column, op_high)

        assert p_high > p_low

    def test_pressure_increases_with_smaller_particles(self, standard_operation):
        col_large = ColumnConfig(
            chemistry='C18', length_mm=150, id_mm=4.6, particle_size_um=5.0,
        )
        col_small = ColumnConfig(
            chemistry='C18', length_mm=150, id_mm=4.6, particle_size_um=1.8,
        )

        p_large = calculate_column_pressure(col_large, standard_operation)
        p_small = calculate_column_pressure(col_small, standard_operation)

        assert p_small > p_large

    def test_pressure_increases_with_longer_column(self, standard_operation):
        col_short = ColumnConfig(
            chemistry='C18', length_mm=50, id_mm=4.6, particle_size_um=5.0,
        )
        col_long = ColumnConfig(
            chemistry='C18', length_mm=250, id_mm=4.6, particle_size_um=5.0,
        )

        p_short = calculate_column_pressure(col_short, standard_operation)
        p_long = calculate_column_pressure(col_long, standard_operation)

        assert p_long > p_short

    def test_pressure_decreases_with_temperature(self, standard_column):
        op_cold = OperationConfig(
            flow_rate_ml_min=1.0, temperature_c=20, injection_volume_ul=10,
        )
        op_hot = OperationConfig(
            flow_rate_ml_min=1.0, temperature_c=60, injection_volume_ul=10,
        )

        p_cold = calculate_column_pressure(standard_column, op_cold)
        p_hot = calculate_column_pressure(standard_column, op_hot)

        assert p_cold > p_hot


class TestPeakWidth:
    def test_peak_width_positive(self, standard_column, standard_operation):
        width = calculate_peak_width(
            retention_time=5.0,
            column=standard_column,
            operation=standard_operation,
        )
        assert width > 0

    def test_longer_retention_wider_peak(self, standard_column, standard_operation):
        w_early = calculate_peak_width(3.0, standard_column, standard_operation)
        w_late = calculate_peak_width(8.0, standard_column, standard_operation)
        assert w_late > w_early


class TestGenerateChromatogram:
    def test_returns_valid_result(
        self, sample_analytes, standard_column,
        standard_mobile, standard_operation,
    ):
        result = generate_chromatogram(
            sample_analytes, standard_column, standard_mobile, standard_operation
        )

        assert len(result.time) > 0
        assert len(result.signal) > 0
        assert len(result.time) == len(result.signal)
        assert result.total_run_time > 0
        assert result.max_pressure_bar >= 0

    def test_overpressure_detected(
        self, sample_analytes, standard_column, standard_mobile,
    ):
        op = OperationConfig(
            flow_rate_ml_min=10.0,
            temperature_c=30,
            injection_volume_ul=10,
        )
        result = generate_chromatogram(
            sample_analytes, standard_column, standard_mobile, op, max_pressure_bar=100
        )
        assert result.overpressure

    def test_peaks_generated(
        self, sample_analytes, standard_column, standard_mobile, standard_operation
    ):
        result = generate_chromatogram(
            sample_analytes, standard_column, standard_mobile, standard_operation
        )
        assert len(result.peaks) > 0

    def test_peak_retention_times_ordered(
        self, sample_analytes, standard_column, standard_mobile, standard_operation
    ):
        result = generate_chromatogram(
            sample_analytes, standard_column, standard_mobile, standard_operation
        )
        retention_times = [p.retention_time for p in result.peaks]
        assert retention_times == sorted(retention_times)


class TestResolution:
    def test_well_separated_peaks(self):
        peaks = [
            PeakInfo(analyte='A', retention_time=2.0, width=0.2, height=1000, area=200),
            PeakInfo(analyte='B', retention_time=5.0, width=0.2, height=1000, area=200),
        ]
        rs, pair = calculate_resolution(peaks)
        assert rs > 1.5

    def test_co_eluting_peaks(self):
        peaks = [
            PeakInfo(analyte='A', retention_time=3.0, width=0.3, height=1000, area=200),
            PeakInfo(analyte='B', retention_time=3.1, width=0.3, height=1000, area=200),
        ]
        rs, pair = calculate_resolution(peaks)
        assert rs < 1.5

    def test_single_peak(self):
        peaks = [
            PeakInfo(analyte='A', retention_time=3.0, width=0.3, height=1000, area=200),
        ]
        rs, pair = calculate_resolution(peaks)
        assert rs == 0.0
        assert pair is None

    def test_critical_pair_identified(self):
        peaks = [
            PeakInfo(analyte='A', retention_time=2.0, width=0.2, height=1000, area=200),
            PeakInfo(analyte='B', retention_time=2.5, width=0.2, height=1000, area=200),
            PeakInfo(analyte='C', retention_time=6.0, width=0.3, height=1000, area=200),
        ]
        rs, pair = calculate_resolution(peaks)
        assert pair == ('A', 'B')


class TestScoring:
    def test_successful_run(self):
        score, success = calculate_score(
            total_run_time=10.0,
            min_resolution=2.0,
            max_pressure_bar=150,
            pressure_limit=400,
            base_points=10000,
        )
        assert success is True
        assert score > 0

    def test_failed_resolution(self):
        score, success = calculate_score(
            total_run_time=10.0,
            min_resolution=1.0,
            max_pressure_bar=150,
            pressure_limit=400,
            base_points=10000,
        )
        assert success is False

    def test_faster_run_scores_higher(self):
        score_slow, _ = calculate_score(
            total_run_time=20.0,
            min_resolution=2.0,
            max_pressure_bar=150,
            base_points=10000,
        )
        score_fast, _ = calculate_score(
            total_run_time=10.0,
            min_resolution=2.0,
            max_pressure_bar=150,
            base_points=10000,
        )
        assert score_fast > score_slow

    def test_higher_resolution_bonus(self):
        score_low, _ = calculate_score(
            total_run_time=10.0,
            min_resolution=1.5,
            max_pressure_bar=150,
            base_points=10000,
        )
        score_high, _ = calculate_score(
            total_run_time=10.0,
            min_resolution=3.0,
            max_pressure_bar=150,
            base_points=10000,
        )
        assert score_high > score_low

    def test_pressure_penalty(self):
        score_low, _ = calculate_score(
            total_run_time=10.0,
            min_resolution=2.0,
            max_pressure_bar=150,
            base_points=10000,
        )
        score_high, _ = calculate_score(
            total_run_time=10.0,
            min_resolution=2.0,
            max_pressure_bar=350,
            base_points=10000,
        )
        assert score_low > score_high


class TestEvaluateRun:
    def test_overpressure_message(self):
        result = evaluate_run(
            total_run_time=10.0,
            min_resolution=2.0,
            max_pressure_bar=500,
            pressure_limit=400,
        )
        assert result.overpressure is True
        assert result.is_successful is False
        assert 'overpressure' in result.message.lower()

    def test_successful_message(self):
        result = evaluate_run(
            total_run_time=10.0,
            min_resolution=2.5,
            max_pressure_bar=150,
        )
        assert result.is_successful is True
        assert result.final_score > 0

    def test_score_breakdown_components(self):
        result = evaluate_run(
            total_run_time=10.0,
            min_resolution=2.0,
            max_pressure_bar=150,
            base_points=10000,
        )
        assert result.base_score > 0
        assert result.pressure_penalty == 0.0
        assert result.resolution_bonus > 0
        assert result.final_score == result.base_score + result.resolution_bonus
