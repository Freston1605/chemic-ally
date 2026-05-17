from django.core.management.base import BaseCommand
from hplc_simulator.models import Analyte, Level


class Command(BaseCommand):
    help = 'Seed HPLC simulator with initial levels and analytes'

    def handle(self, *args, **options):
        self.stdout.write('Creating analytes...')
        analytes = self._create_analytes()

        self.stdout.write('Creating levels...')
        self._create_levels(analytes)

        self.stdout.write(self.style.SUCCESS('Successfully seeded HPLC simulator data'))

    def _create_analytes(self):
        analyte_data = [
            {
                'name': 'Caffeine',
                'formula': 'C8H10N4O2',
                'pka': 0.6,
                'log_p': -0.07,
                'log_kw': -1.0,
                's_parameter': 2.5,
                'molecular_weight': 194.19,
                'uv_absorption_max': 272,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 10000,
            },
            {
                'name': 'Acetaminophen',
                'formula': 'C8H9NO2',
                'pka': 9.38,
                'log_p': 0.46,
                'log_kw': -0.3,
                's_parameter': 2.8,
                'molecular_weight': 151.16,
                'uv_absorption_max': 243,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 12000,
            },
            {
                'name': 'Ibuprofen',
                'formula': 'C13H18O2',
                'pka': 4.91,
                'log_p': 3.97,
                'log_kw': 0.4,
                's_parameter': 3.0,
                'molecular_weight': 206.28,
                'uv_absorption_max': 222,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 500,
            },
            {
                'name': 'Aspirin',
                'formula': 'C9H8O4',
                'pka': 3.5,
                'log_p': 1.19,
                'log_kw': 0.1,
                's_parameter': 3.0,
                'molecular_weight': 180.16,
                'uv_absorption_max': 276,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 4000,
            },
            {
                'name': 'Naproxen',
                'formula': 'C14H14O3',
                'pka': 4.15,
                'log_p': 3.18,
                'log_kw': 0.3,
                's_parameter': 3.0,
                'molecular_weight': 230.26,
                'uv_absorption_max': 230,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 6000,
            },
            {
                'name': 'Benzene',
                'formula': 'C6H6',
                'pka': None,
                'log_p': 2.13,
                'log_kw': -0.5,
                's_parameter': 2.5,
                'molecular_weight': 78.11,
                'uv_absorption_max': 254,
                'neutral_charge': True,
                'concentration_mm': 1.0,
                'extinction_coefficient': 200,
            },
            {
                'name': 'Toluene',
                'formula': 'C7H8',
                'pka': None,
                'log_p': 2.73,
                'log_kw': -0.1,
                's_parameter': 2.7,
                'molecular_weight': 92.14,
                'uv_absorption_max': 261,
                'neutral_charge': True,
                'concentration_mm': 1.0,
                'extinction_coefficient': 230,
            },
            {
                'name': 'Ethylbenzene',
                'formula': 'C8H10',
                'pka': None,
                'log_p': 3.15,
                'log_kw': 0.2,
                's_parameter': 2.8,
                'molecular_weight': 106.17,
                'uv_absorption_max': 260,
                'neutral_charge': True,
                'concentration_mm': 1.0,
                'extinction_coefficient': 260,
            },
            {
                'name': 'Naphthalene',
                'formula': 'C10H8',
                'pka': None,
                'log_p': 3.3,
                'log_kw': 0.5,
                's_parameter': 3.0,
                'molecular_weight': 128.17,
                'uv_absorption_max': 220,
                'neutral_charge': True,
                'concentration_mm': 1.0,
                'extinction_coefficient': 5000,
            },
            {
                'name': 'Phenol',
                'formula': 'C6H5OH',
                'pka': 9.95,
                'log_p': 1.46,
                'log_kw': -0.2,
                's_parameter': 3.7,
                'molecular_weight': 94.11,
                'uv_absorption_max': 270,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 1450,
            },
            {
                'name': 'Benzoic Acid',
                'formula': 'C7H6O2',
                'pka': 4.2,
                'log_p': 1.87,
                'log_kw': 0.0,
                's_parameter': 3.0,
                'molecular_weight': 122.12,
                'uv_absorption_max': 230,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 12000,
            },
            {
                'name': 'Aniline',
                'formula': 'C6H5NH2',
                'pka': 4.6,
                'log_p': 0.9,
                'log_kw': -0.4,
                's_parameter': 2.6,
                'molecular_weight': 93.13,
                'uv_absorption_max': 230,
                'neutral_charge': False,
                'concentration_mm': 1.0,
                'extinction_coefficient': 1400,
            },
        ]

        created = {}
        for data in analyte_data:
            obj, is_new = Analyte.objects.update_or_create(
                name=data['name'],
                defaults=data,
            )
            created[data['name']] = obj
            if is_new:
                self.stdout.write(f'  Created analyte: {obj.name}')
            else:
                self.stdout.write(f'  Updated analyte: {obj.name}')

        return created

    def _create_levels(self, analytes):
        levels = [
            {
                'name': 'Painkiller Analysis',
                'slug': 'painkiller-analysis',
                'description': (
                    'Separate a mixture of common over-the-counter analgesics: '
                    'caffeine, acetaminophen, ibuprofen, and aspirin.'
                ),
                'difficulty': 'beginner',
                'analytes': ['Caffeine', 'Acetaminophen', 'Ibuprofen', 'Aspirin'],
                'available_columns': ['C18', 'C8'],
                'max_pressure_bar': 400.0,
                'base_points': 10000.0,
            },
            {
                'name': 'Environmental Pollutants',
                'slug': 'environmental-pollutants',
                'description': (
                    'Separate a mixture of polycyclic aromatic hydrocarbons '
                    '(PAHs) found in environmental samples.'
                ),
                'difficulty': 'beginner',
                'analytes': ['Benzene', 'Toluene', 'Ethylbenzene', 'Naphthalene'],
                'available_columns': ['C18'],
                'max_pressure_bar': 400.0,
                'base_points': 10000.0,
            },
            {
                'name': 'NSAID Comparison',
                'slug': 'nsaid-comparison',
                'description': (
                    'Optimize separation of three common non-steroidal '
                    'anti-inflammatory drugs with similar properties.'
                ),
                'difficulty': 'intermediate',
                'analytes': ['Ibuprofen', 'Naproxen', 'Aspirin'],
                'available_columns': ['C18', 'C8', 'Phenyl'],
                'max_pressure_bar': 400.0,
                'base_points': 12000.0,
            },
            {
                'name': 'Phenolic Compounds',
                'slug': 'phenolic-compounds',
                'description': (
                    'Separate a mixture of phenolic compounds. '
                    'pH control is critical for ionizable analytes.'
                ),
                'difficulty': 'intermediate',
                'analytes': ['Phenol', 'Benzoic Acid', 'Aniline', 'Acetaminophen'],
                'available_columns': ['C18', 'C8'],
                'max_pressure_bar': 400.0,
                'base_points': 12000.0,
            },
            {
                'name': 'Full Painkiller Panel',
                'slug': 'full-painkiller-panel',
                'description': (
                    'Separate all five analgesics including naproxen. '
                    'Achieving baseline resolution for all peaks is challenging.'
                ),
                'difficulty': 'advanced',
                'analytes': [
                    'Caffeine', 'Acetaminophen', 'Ibuprofen',
                    'Aspirin', 'Naproxen',
                ],
                'available_columns': ['C18', 'C8', 'Phenyl'],
                'max_pressure_bar': 400.0,
                'base_points': 15000.0,
            },
            {
                'name': 'Method Transfer Challenge',
                'slug': 'method-transfer',
                'description': (
                    'Transfer a legacy 20-minute method to a modern UHPLC '
                    'method under 5 minutes while maintaining resolution.'
                ),
                'difficulty': 'expert',
                'analytes': [
                    'Benzene', 'Toluene', 'Ethylbenzene',
                    'Naphthalene', 'Phenol',
                ],
                'available_columns': ['C18'],
                'max_pressure_bar': 600.0,
                'base_points': 20000.0,
            },
        ]

        for data in levels:
            analyte_names = data.pop('analytes')
            obj, is_new = Level.objects.get_or_create(
                slug=data['slug'],
                defaults=data,
            )
            obj.analytes.set([analytes[name] for name in analyte_names])

            if is_new:
                self.stdout.write(f'  Created level: {obj.name}')
            else:
                self.stdout.write(f'  Updated level: {obj.name}')
