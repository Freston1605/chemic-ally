from django.db import models


class Analyte(models.Model):
    """Chemical analyte with properties affecting chromatographic behavior."""

    name = models.CharField(max_length=100)
    formula = models.CharField(
        max_length=50, blank=True,
        help_text="Chemical formula, e.g. C8H9NO2",
    )
    pka = models.FloatField(
        null=True, blank=True,
        help_text="Acid dissociation constant",
    )
    log_p = models.FloatField(help_text="Octanol-water partition coefficient")
    log_kw = models.FloatField(
        help_text="Log of retention factor in pure water (LSS model)",
    )
    s_parameter = models.FloatField(
        default=4.0,
        help_text="LSS model S parameter (slope of log k vs organic fraction)"
    )
    molecular_weight = models.FloatField(help_text="Molecular weight in g/mol")
    uv_absorption_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Wavelength of maximum UV absorption in nm"
    )
    neutral_charge = models.BooleanField(
        default=True,
        help_text="Whether analyte is neutral at typical HPLC pH"
    )
    concentration_mm = models.FloatField(
        default=1.0,
        help_text="Analyte concentration in mM for peak height calculation"
    )
    extinction_coefficient = models.FloatField(
        null=True, blank=True,
        help_text="Molar extinction coefficient (L/mol/cm) at UV absorption max"
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Level(models.Model):
    """A challenge level defining a mixture of analytes to separate."""

    DIFFICULTY_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
        ('expert', 'Expert'),
    ]

    COLUMN_CHEMISTRIES = [
        ('C18', 'C18 (Reversed Phase)'),
        ('C8', 'C8 (Reversed Phase)'),
        ('C4', 'C4 (Reversed Phase)'),
        ('HILIC', 'HILIC'),
        ('NP', 'Normal Phase'),
        ('Phenyl', 'Phenyl-Hexyl'),
    ]

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, help_text="URL-friendly identifier")
    description = models.TextField()
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES)
    analytes = models.ManyToManyField(Analyte, related_name='levels')
    available_columns = models.JSONField(
        default=list,
        help_text="List of available column chemistry codes"
    )
    max_pressure_bar = models.FloatField(
        default=400.0,
        help_text="Maximum system pressure in bar"
    )
    solvent_a = models.CharField(
        max_length=100,
        default="Water (0.1% Formic Acid)",
        help_text="Description of aqueous mobile phase"
    )
    solvent_b = models.CharField(
        max_length=100,
        default="Acetonitrile",
        help_text="Description of organic mobile phase"
    )
    base_points = models.FloatField(
        default=10000.0,
        help_text="Base points for scoring calculation"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['difficulty', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_difficulty_display()})"


class UserScore(models.Model):
    """Record of a user's simulation run."""

    level = models.ForeignKey(Level, on_delete=models.CASCADE, related_name='scores')
    session_key = models.CharField(max_length=40, db_index=True)

    # Parameters used for this run
    mobile_phase = models.JSONField(help_text="Mobile phase configuration")
    column_config = models.JSONField(help_text="Column configuration")
    operation_config = models.JSONField(help_text="Operational settings")

    # Results
    total_run_time = models.FloatField(help_text="Total run time in minutes")
    max_pressure_bar = models.FloatField(help_text="Maximum pressure reached in bar")
    min_resolution = models.FloatField(
        help_text="Minimum resolution between adjacent peaks",
    )
    critical_pair = models.JSONField(
        null=True,
        blank=True,
        help_text="Names of the two analytes forming the critical pair"
    )
    score = models.FloatField(help_text="Calculated score for this run")
    is_successful = models.BooleanField(
        default=False,
        help_text="Whether Rs >= 1.5 for all adjacent pairs"
    )
    overpressure = models.BooleanField(
        default=False,
        help_text="Whether the run exceeded max pressure"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-score']
        indexes = [
            models.Index(fields=['level', 'session_key']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_pressure_bar__gte=0),
                name='userscore_max_pressure_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(min_resolution__gte=0),
                name='userscore_min_resolution_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(total_run_time__gte=0),
                name='userscore_total_run_time_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=0),
                name='userscore_score_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.level.name} - Score: {self.score:.0f}"

    def clean(self):
        super().clean()
        errors = {}
        if self.max_pressure_bar < 0:
            errors['max_pressure_bar'] = 'Pressure cannot be negative'
        if self.min_resolution < 0:
            errors['min_resolution'] = 'Resolution cannot be negative'
        if self.total_run_time <= 0:
            errors['total_run_time'] = 'Run time must be positive'
        if self.score < 0:
            errors['score'] = 'Score cannot be negative'
        if (
            self.max_pressure_bar > self.level.max_pressure_bar
            and not self.overpressure
        ):
            errors['overpressure'] = (
                f'Pressure {self.max_pressure_bar:.0f} bar exceeds level limit '
                f'{self.level.max_pressure_bar:.0f} bar; overpressure flag must be set'
            )
        self._validate_operation_config(errors)
        self._validate_column_config(errors)
        if errors:
            from django.core.exceptions import ValidationError
            raise ValidationError(errors)

    def _validate_operation_config(self, errors):
        op = self.operation_config or {}
        flow = op.get('flow_rate_ml_min')
        if flow is not None and flow <= 0:
            errors['operation_config'] = 'Flow rate must be strictly positive'
        temp = op.get('temperature_c')
        if temp is not None and (temp < 20 or temp > 80):
            errors.setdefault('operation_config', '')
            errors['operation_config'] += ' Temperature must be 20-80 C'
        inj = op.get('injection_volume_ul')
        if inj is not None and (inj < 1 or inj > 100):
            errors.setdefault('operation_config', '')
            errors['operation_config'] += ' Injection volume must be 1-100 uL'

    def _validate_column_config(self, errors):
        col = self.column_config or {}
        chemistry = col.get('chemistry', '')
        if chemistry in ('HILIC', 'NP'):
            errors['column_config'] = (
                f'{chemistry} retention model is not implemented. '
                'Only reversed-phase (C18, C8, C4, Phenyl) is supported.'
            )


class LevelProgress(models.Model):
    """Tracks a user's best progress per level."""

    level = models.ForeignKey(
        Level, on_delete=models.CASCADE,
        related_name='progress_records',
    )
    session_key = models.CharField(max_length=40, db_index=True)
    best_score = models.FloatField(default=0.0)
    best_resolution = models.FloatField(default=0.0)
    best_run_time = models.FloatField(default=0.0)
    attempts = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(
        default=False,
        help_text="Whether the user has achieved baseline resolution (Rs >= 1.5)"
    )
    last_attempt_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['level', 'session_key']
        ordering = ['level__difficulty', 'level__name']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(best_score__gte=0),
                name='levelprogress_best_score_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(best_resolution__gte=0),
                name='levelprogress_best_resolution_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(best_run_time__gte=0),
                name='levelprogress_best_run_time_non_negative',
            ),
        ]

    def __str__(self):
        status = "Completed" if self.completed else "In Progress"
        return f"{self.level.name} - {status} (Best: {self.best_score:.0f})"
