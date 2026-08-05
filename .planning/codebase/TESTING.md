# Testing Patterns

**Analysis Date:** 2026-08-05

## Test Framework

**Runner:**
- pytest 8.4.0 with pytest-django 4.11.1 (pinned in `requirements/development.txt`)
- Config: `pytest.ini` — `DJANGO_SETTINGS_MODULE = config.settings.development`, `pythonpath = .`, `python_files = test_*.py *_test.py`
- Django runner (`python manage.py test`) is documented in AGENTS.md for `chemistry_calculators` only — the HPLC tests are pytest-only and will not run under the Django runner
- **No `conftest.py` exists anywhere** (verified by search) — fixtures are defined per test module
- No `pyproject.toml`, `setup.cfg`, or `tox.ini` with pytest config

**Assertion Library:**
- Two styles coexist:
  1. `unittest` assertions (`assertEqual`, `assertAlmostEqual`, `assertIsNone`, `assertIn`, `assertTemplateUsed`) — used in `tests/chemistry_calculators/test_webapp.py` inside Django `TestCase`/`SimpleTestCase` classes
  2. Plain `assert` statements with pytest — used in all `tests/hplc_simulator/*.py`

**Run Commands:**
```bash
pytest -v                        # Run all 108 tests (CI runs this)
python manage.py test chemistry_calculators   # Django runner, chemistry_calculators only
flake8 .                         # Lint (CI runs before tests)
```

## Test File Organization

**Location:**
- Project-level `tests/` directory mirroring app names: `tests/chemistry_calculators/`, `tests/hplc_simulator/`
- NOTE: HPLC tests live under `tests/hplc_simulator/`, NOT inside `apps/hplc_simulator/tests/` (the `apps/hplc_simulator/tests/` package exists but is empty — only `__init__.py`)
- Django `TestCase`-based webapp tests are in `tests/chemistry_calculators/test_webapp.py` (a single 608-line file covering calculators, forms, views, context processors, and logging config)

**Naming:**
- Files: `test_<module>.py` — `test_engine.py`, `test_models.py`, `test_validation.py`, `test_webapp.py`
- Classes: `Test<Concern>` or `<Concern>Tests` — `TestRetentionFactorIsocratic`, `TestUserScoreConstraints`, `FormTests`, `ViewTests`
- Methods: `test_<behavior>` — `test_negative_pressure_rejected`, `test_well_separated_peaks`

**Structure:**
```
tests/
├── __init__.py
├── chemistry_calculators/
│   └── test_webapp.py            # Django TestCase/SimpleTestCase style
└── hplc_simulator/
    ├── test_engine.py            # pytest fixtures + plain asserts
    ├── test_models.py            # DB CHECK constraint tests (db fixture)
    └── test_validation.py        # serializer + Model.clean() tests
```

## Test Structure

**Suite Organization — two distinct patterns:**

Pattern A (Django classes, `tests/chemistry_calculators/test_webapp.py`):
```python
class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_molecular_weight_view_get(self):
        response = self.client.get(reverse("molecular_weight"))
        self.assertEqual(response.status_code, 200)
```

Pattern B (pytest classes + fixtures, `tests/hplc_simulator/test_engine.py`):
```python
@pytest.fixture
def sample_analytes():
    return [AnalyteProperties(name='Caffeine', log_kw=0.5, s_parameter=3.5, ...)]

class TestColumnPressure:
    def test_pressure_increases_with_flow_rate(self, standard_column):
        op_low = OperationConfig(flow_rate_ml_min=0.5, temperature_c=30, injection_volume_ul=10)
        op_high = OperationConfig(flow_rate_ml_min=2.0, temperature_c=30, injection_volume_ul=10)
        assert calculate_column_pressure(standard_column, op_high) > \
               calculate_column_pressure(standard_column, op_low)
```

**Patterns:**
- **Setup:** `setUp(self)` with `self.client = Client()` for view tests; `setUp` assigning calculators — `self.calc = EquilibriaCalculator()` (`test_webapp.py:277-278`); pytest fixtures for HPLC engine config objects
- **Teardown:** None used anywhere — no `tearDown`, no yield fixtures
- **Assertion pattern (float comparisons):** `self.assertAlmostEqual(x, expected, places=3)` (Django style) vs `assert abs(k - expected) < 0.001` (pytest style)
- **DB access:** pytest `db` fixture requested explicitly (`def test_negative_pressure_rejected(self, db, sample_level, valid_score_data):`) rather than Django `TestCase`
- **Response context assertions:** `response.context["result"]`, `response.context.get("form")` — used to verify view output without rendering assertions
- `settings.SECRET_KEY = "test"` is set at module level in `test_webapp.py:21` before classes

## Mocking

**Framework:** None. No `unittest.mock`, `pytest-mock`, or `mocker` usage anywhere in the test suite (verified by grep).

**Patterns:** Not applicable — no mocks exist.

**What to Mock:**
- No guidance exists in-repo. Current tests avoid external dependencies entirely by testing pure functions and using the test DB.
- If mocking is needed in new tests, introduce it deliberately: prefer `unittest.mock.patch` (stdlib) or add `pytest-mock` for the `mocker` fixture; keep it minimal and consistent with the existing "no mock" suite.

**What NOT to Mock:**
- The existing suite tests real integrations: real chempy calculations (`chempy.Substance`), real Pint conversions, real SQLite DB via the `db` fixture, real Django test client and session.

## Fixtures and Factories

**Test Data — pytest fixtures** (defined inline per module in `tests/hplc_simulator/`):
```python
@pytest.fixture
def sample_level(db):
    return Level.objects.create(
        name='Test Level', slug='test-level',
        description='Test', difficulty='beginner',
        available_columns=['C18'], max_pressure_bar=400.0, base_points=10000.0,
    )

@pytest.fixture
def valid_score_data():
    return {
        'mobile_phase': {'start_b': 5, 'end_b': 95, 'ramp_time': 20, 'ph': 3.0},
        'column_config': {'chemistry': 'C18', 'length_mm': 150, 'id_mm': 4.6, 'particle_size_um': 5.0},
        'operation_config': {'flow_rate_ml_min': 1.0, 'temperature_c': 30, 'injection_volume_ul': 10},
        'total_run_time': 25.0, 'max_pressure_bar': 120.0, 'min_resolution': 1.8,
        'score': 400.0, 'is_successful': True, 'overpressure': False,
    }
```
- Helper methods used as factories inside test classes: `_valid_reactions_json()` (`test_webapp.py:391-412`), `_valid_payload(**overrides)` (`test_validation.py:105-128`)
- Shared fixture names reused across modules but **re-defined per module** (no conftest): `sample_level` appears in both `test_models.py` and `test_validation.py` with different slugs (`'test-level'` vs `'test-level-django'`) to avoid cross-test collisions
- DB records are built directly with `Model.objects.create(...)` — no `factory_boy`

**Location:** Fixtures live in the test files that use them. Seed/production data lives separately in `apps/hplc_simulator/management/commands/seed_hplc_data.py` (not used by tests).

## Coverage

**Requirements:** None enforced — no `.coveragerc`, no `--cov` flags in `pytest.ini` or CI, no coverage dependency installed.

**View Coverage:**
```bash
pip install pytest-cov
pytest --cov=apps --cov=chemistry_calculators --cov=hplc_simulator
```
(Not configured; would need to be added.)

## Test Types

**Unit Tests:**
- Calculation engines: `MolecularWeightCalculator`, `ReactionBalancer`, `DilutionCalculator`, `EquilibriaCalculator` (`test_webapp.py:24-98, 274-385`)
- HPLC engine pure functions: `calculate_retention_factor_isocratic`, `calculate_effective_retention_gradient`, `calculate_column_pressure`, `calculate_peak_width`, `calculate_resolution`, `calculate_score`, `evaluate_run`, `_calculate_peak_height`, `generate_chromatogram` (`tests/hplc_simulator/test_engine.py`)
- Serializer validation: `ColumnConfigSerializer`, `OperationConfigSerializer`, `SimulationRequestSerializer` (`test_validation.py:24-157`)

**Integration Tests:**
- Form + model validation: `EquilibriumSystemForm` JSON parsing/unit conversion (`test_webapp.py:388-542`), `UserScore.clean()` cross-field validation (`test_validation.py:160-250`)
- DB CHECK constraints against real SQLite: `IntegrityError` expected for negative pressure/resolution/run-time/score (`test_models.py`)
- View + template rendering via Django test client: GET/POST flows asserting status codes, `response.context`, `assertTemplateUsed` (`test_webapp.py:195-254, 545-601`)
- Context processor + session integration (`test_webapp.py:257-271`)
- Logging configuration test asserting `settings.LOGGING` structure (`test_webapp.py:603-608`)

**E2E Tests:** Not used. No Playwright/Selenium. HPLC API views (`SimulateView`, `ScoreSubmissionView`, `UserScoresView`, `LevelProgressView`) have **no test coverage**.

## Common Patterns

**Async Testing:** Not applicable — no asyncio code exists.

**Error Testing:**
```python
# DB constraint failure (pytest style)
with pytest.raises(IntegrityError):
    UserScore.objects.create(level=sample_level, session_key='test-session-1', **valid_score_data)

# Model.clean() failure — assert message_dict keys (pytest style)
with pytest.raises(ValidationError) as exc_info:
    score.full_clean()
assert 'overpressure' in exc_info.value.message_dict

# Form errors (Django unittest style)
form = ChemicalReactionForm({"reactant": "", "product": "", "reversible": True})
self.assertFalse(form.is_valid())
self.assertIn("Reactant and product must be provided.", form.errors["__all__"][0])
```

**View Testing Pattern:**
```python
def test_dilution_view_post_missing_c2_returns_requested_unit(self):
    data = {"c1": "1", "c1_unit": "mol/L", "v1": "1", "v1_unit": "L",
            "c2": "", "c2_unit": "mol/L", "v2": "2", "v2_unit": "L"}
    response = self.client.post(reverse("dilution"), data)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.context["result"]["property"], "Final Concentration")
    self.assertAlmostEqual(response.context["result"]["value"], 0.5)
```

**Parameterized Data:** Not used — no `@pytest.mark.parametrize` anywhere; repetition via helper methods instead (e.g., `valid_score_data['max_pressure_bar'] = -10.0` per test).

## CI Test Execution

- `.github/workflows/deploy.yml` test job: `cp .env.example .env` → `python manage.py check --deploy` → `pytest -v`
- Pipeline order: lint (`flake8 .`) → test (`pytest -v` + deploy check) → security scan (pip-audit, safety) → Docker build → SAM deploy
- Runs on push/PR to `main`; Python 3.14

## Coverage Gaps (observed, not enforced)

- `apps/hplc_simulator/views.py` API endpoints — zero tests (SimulateView, ScoreSubmissionView, UserScoresView, LevelProgressView)
- `apps/hplc_simulator/forms.py` (`SimulationParameterForm`) — zero tests
- `apps/hplc_simulator/management/commands/seed_hplc_data.py` — zero tests
- `apps/chemistry_calculators/urls.py` / `apps/hplc_simulator/urls.py` — no URL resolution tests
- `config/storage_backends.py`, `config/settings/production.py` — zero tests
- `chemically/chemically/context_processors.py::current_url` — untested (only `previous_substances` covered)
- No `__str__`/`Meta.ordering` model tests beyond DB constraint coverage

---

*Testing analysis: 2026-08-05*
