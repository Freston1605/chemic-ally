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

    def __str__(self):
        return f"{self.level.name} - Score: {self.score:.0f}"


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

    def __str__(self):
        status = "Completed" if self.completed else "In Progress"
        return f"{self.level.name} - {status} (Best: {self.best_score:.0f})"
