from rest_framework import serializers
from .models import Analyte, Level, UserScore


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
    chromatogram = serializers.DictField(
        child=serializers.ListField(child=serializers.FloatField())
    )
    peaks = PeakSerializer(many=True)
    metrics = MetricsSerializer()
    score = serializers.FloatField()
    success = serializers.BooleanField()
    overpressure = serializers.BooleanField()
    message = serializers.CharField()


class ScoreSubmissionSerializer(serializers.Serializer):
    level_id = serializers.IntegerField(min_value=1)
    score = serializers.FloatField(min_value=0)
    total_run_time = serializers.FloatField(min_value=0)
    min_resolution = serializers.FloatField(min_value=0)
    max_pressure_bar = serializers.FloatField(min_value=0)
    is_successful = serializers.BooleanField()
    overpressure = serializers.BooleanField()
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
        model = UserScore
        fields = [
            'id', 'level_name', 'difficulty', 'best_score',
            'best_resolution', 'best_run_time', 'attempts',
            'completed', 'last_attempt_at',
        ]
