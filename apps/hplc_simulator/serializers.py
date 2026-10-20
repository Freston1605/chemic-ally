from rest_framework import serializers
from .models import Analyte, Level, LevelProgress, UserScore


class AnalyteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Analyte
        fields = [
            'id', 'name', 'formula', 'pka', 'log_p', 'log_kw',
            's_parameter', 'molecular_weight', 'uv_absorption_max',
            'neutral_charge', 'concentration_mm', 'extinction_coefficient',
        ]


class LevelListSerializer(serializers.ModelSerializer):
    analyte_count = serializers.SerializerMethodField()

    class Meta:
        model = Level
        fields = [
            'id', 'name', 'slug', 'description', 'difficulty',
            'analyte_count', 'max_pressure_bar',
        ]

    def get_analyte_count(self, obj):
        return obj.analytes.count()


class LevelDetailSerializer(serializers.ModelSerializer):
    analytes = AnalyteSerializer(many=True, read_only=True)
    constraints = serializers.SerializerMethodField()

    class Meta:
        model = Level
        fields = [
            'id', 'name', 'slug', 'description', 'difficulty',
            'analytes', 'constraints', 'base_points',
        ]

    def get_constraints(self, obj):
        return {
            'available_columns': obj.available_columns,
            'max_pressure_bar': obj.max_pressure_bar,
            'solvent_a': obj.solvent_a,
            'solvent_b': obj.solvent_b,
        }


class MobilePhaseSerializer(serializers.Serializer):
    start_b = serializers.FloatField(min_value=0, max_value=100)
    end_b = serializers.FloatField(min_value=0, max_value=100)
    ramp_time = serializers.FloatField(min_value=0.5, max_value=60)
    ph = serializers.FloatField(min_value=2.0, max_value=8.0)
    buffer_concentration_mm = serializers.FloatField(min_value=1, max_value=100)
    is_gradient = serializers.BooleanField(required=False, default=True)

    def validate(self, data):
        if data['end_b'] < data['start_b']:
            raise serializers.ValidationError(
                "end_b must be greater than or equal to start_b"
            )
        return data


class ColumnConfigSerializer(serializers.Serializer):
    chemistry = serializers.CharField()
    length_mm = serializers.FloatField()
    id_mm = serializers.FloatField()
    particle_size_um = serializers.FloatField()

    def validate(self, data):
        data = super().validate(data)
        if data['chemistry'] in ('HILIC', 'NP'):
            raise serializers.ValidationError(
                f"{data['chemistry']} retention model is not implemented. "
                "Only reversed-phase (C18, C8, C4, Phenyl) is supported."
            )
        return data

    def validate_length_mm(self, value):
        valid = [50, 100, 150, 250]
        if value not in valid:
            raise serializers.ValidationError(
                f"Invalid column length. Choose from {valid}"
            )
        return value

    def validate_id_mm(self, value):
        valid = [2.1, 3.0, 4.6]
        if value not in valid:
            raise serializers.ValidationError(
                f"Invalid column ID. Choose from {valid}"
            )
        return value

    def validate_particle_size_um(self, value):
        valid = [1.8, 3.0, 5.0]
        if value not in valid:
            raise serializers.ValidationError(
                f"Invalid particle size. Choose from {valid}"
            )
        return value


class OperationConfigSerializer(serializers.Serializer):
    flow_rate_ml_min = serializers.FloatField(min_value=0.1, max_value=5.0)
    temperature_c = serializers.FloatField(min_value=20, max_value=80)
    injection_volume_ul = serializers.FloatField(min_value=1, max_value=100)
    dwell_time_min = serializers.FloatField(
        min_value=0.1, max_value=10.0, default=1.0, required=False,
    )

    def validate_flow_rate_ml_min(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Flow rate must be strictly positive"
            )
        return value


class SimulationRequestSerializer(serializers.Serializer):
    level_id = serializers.IntegerField(min_value=1)
    mobile_phase = MobilePhaseSerializer()
    column = ColumnConfigSerializer()
    operation = OperationConfigSerializer()

    def validate(self, data):
        data = super().validate(data)
        ph = data['mobile_phase']['ph']
        chemistry = data['column']['chemistry']
        if chemistry in ('C18', 'C8', 'C4') and ph > 7.5:
            raise serializers.ValidationError({
                'mobile_phase': {
                    'ph': (
                        f"pH {ph} exceeds safe range for {chemistry} "
                        f"silica columns (max ~7.5)"
                    )
                }
            })
        return data


class PeakSerializer(serializers.Serializer):
    analyte = serializers.CharField()
    retention_time = serializers.FloatField()
    width = serializers.FloatField()
    height = serializers.FloatField()
    area = serializers.FloatField()


class MetricsSerializer(serializers.Serializer):
    total_run_time = serializers.FloatField()
    max_pressure_bar = serializers.FloatField()
    min_resolution = serializers.FloatField()
    critical_pair = serializers.ListField(
        child=serializers.CharField(),
        allow_null=True,
    )


class SimulationResponseSerializer(serializers.Serializer):
    """Response body of ``POST /hplc/api/simulate/``.

    ``result_token`` (R013, D021) is the server-signed summary of the run the
    server itself computed. Clients MUST echo this value verbatim as the
    ``result_token`` field of ``POST /hplc/api/scores/``: it is the only
    trusted carrier of the run's numbers, and a tampered, expired or replayed
    token is refused there. The field is declared last so the response key
    order stays additive for existing clients.
    """

    chromatogram = serializers.DictField(
        child=serializers.ListField(child=serializers.FloatField())
    )
    peaks = PeakSerializer(many=True)
    metrics = MetricsSerializer()
    score = serializers.FloatField()
    success = serializers.BooleanField()
    overpressure = serializers.BooleanField()
    message = serializers.CharField()
    result_token = serializers.CharField()


class ScoreSubmissionSerializer(serializers.Serializer):
    """Request body of ``POST /hplc/api/scores/`` -- token-authoritative (D022).

    ``result_token`` (R013/R014) is the ONLY trusted carrier of the run's
    numbers: the view verifies it, recomputes the score server-side, and
    stores those numbers. It is declared required, but the view checks the
    token BEFORE consulting this serializer, so a missing token is answered by
    the view's uniform non-disclosing 400 rather than a DRF-shaped field
    error -- both properties (required field, uniform rejection) hold.

    ``score``, ``is_successful`` and ``overpressure`` are accepted for
    backward compatibility with the already-deployed browser client
    (``static/hplc_simulator/js/simulator.js`` ``submitScore``, which predates
    the token) and are **never read**: they are write-only and ignored, so a
    stale tab keeps working while a patched one can no longer influence the
    persisted result. ``overpressure`` is mirrored from the signed payload in
    the view, not taken from here.
    """

    level_id = serializers.IntegerField(min_value=1)
    result_token = serializers.CharField(max_length=512)
    # Accepted-and-ignored legacy fields (D022). Never read by the view.
    score = serializers.FloatField(required=False, write_only=True)
    is_successful = serializers.BooleanField(required=False, write_only=True)
    overpressure = serializers.BooleanField(required=False, write_only=True)
    total_run_time = serializers.FloatField(min_value=0)
    min_resolution = serializers.FloatField(min_value=0)
    max_pressure_bar = serializers.FloatField(min_value=0)
    mobile_phase = serializers.JSONField()
    column_config = serializers.JSONField()
    operation_config = serializers.JSONField()


class UserScoreSerializer(serializers.ModelSerializer):
    level_name = serializers.CharField(source='level.name', read_only=True)
    difficulty = serializers.CharField(source='level.difficulty', read_only=True)

    class Meta:
        model = UserScore
        fields = [
            'id', 'level_name', 'difficulty', 'score', 'total_run_time',
            'min_resolution', 'max_pressure_bar', 'is_successful',
            'overpressure', 'created_at',
        ]


class LevelProgressSerializer(serializers.ModelSerializer):
    level_name = serializers.CharField(source='level.name', read_only=True)
    difficulty = serializers.CharField(source='level.difficulty', read_only=True)

    class Meta:
        model = LevelProgress
        fields = [
            'id', 'level_name', 'difficulty', 'best_score',
            'best_resolution', 'best_run_time', 'attempts',
            'completed', 'last_attempt_at',
        ]
