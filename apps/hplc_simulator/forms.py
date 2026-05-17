from django import forms


class SimulationParameterForm(forms.Form):
    """Validates HPLC simulation parameters."""

    level_id = forms.IntegerField(min_value=1)

    start_b = forms.FloatField(
        min_value=0,
        max_value=100,
        initial=5,
        help_text="Starting organic modifier percentage"
    )
    end_b = forms.FloatField(
        min_value=0,
        max_value=100,
        initial=95,
        help_text="Ending organic modifier percentage"
    )
    ramp_time = forms.FloatField(
        min_value=0.5,
        max_value=60,
        initial=20,
        help_text="Gradient ramp time in minutes"
    )
    ph = forms.FloatField(
        min_value=2.0,
        max_value=8.0,
        initial=3.0,
        help_text="Mobile phase pH"
    )
    buffer_concentration_mm = forms.FloatField(
        min_value=1,
        max_value=100,
        initial=10,
        help_text="Buffer concentration in mM"
    )
    is_gradient = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Use gradient elution"
    )

    column_chemistry = forms.ChoiceField(
        choices=[
            ('C18', 'C18'),
            ('C8', 'C8'),
            ('C4', 'C4'),
            ('HILIC', 'HILIC'),
            ('NP', 'Normal Phase'),
            ('Phenyl', 'Phenyl-Hexyl'),
        ],
        initial='C18',
    )
    column_length_mm = forms.ChoiceField(
        choices=[
            (50, '50 mm'),
            (100, '100 mm'),
            (150, '150 mm'),
            (250, '250 mm'),
        ],
        initial=150,
        coerce=float,
    )
    column_id_mm = forms.ChoiceField(
        choices=[
            (2.1, '2.1 mm'),
            (3.0, '3.0 mm'),
            (4.6, '4.6 mm'),
        ],
        initial=4.6,
        coerce=float,
    )
    particle_size_um = forms.ChoiceField(
        choices=[
            (1.8, '1.8 um'),
            (3.0, '3.0 um'),
            (5.0, '5.0 um'),
        ],
        initial=5.0,
        coerce=float,
    )

    flow_rate_ml_min = forms.FloatField(
        min_value=0.1,
        max_value=5.0,
        initial=1.0,
        help_text="Flow rate in mL/min"
    )
    temperature_c = forms.FloatField(
        min_value=20,
        max_value=80,
        initial=30,
        help_text="Column temperature in Celsius"
    )
    injection_volume_ul = forms.FloatField(
        min_value=1,
        max_value=100,
        initial=10,
        help_text="Injection volume in uL"
    )

    def clean(self):
        cleaned_data = super().clean()
        start_b = cleaned_data.get('start_b')
        end_b = cleaned_data.get('end_b')

        if start_b is not None and end_b is not None:
            if end_b < start_b:
                self.add_error(
                    'end_b',
                    'End B% must be greater than or equal to start B%'
                )

        return cleaned_data
