# Coding Conventions

**Analysis Date:** 2026-08-05

## Naming Patterns

**Files:**
- Modules: `snake_case.py` — `views.py`, `forms.py`, `models.py`, `serializers.py`, `engine.py`
- App packages live under `apps/<app_name>/` (e.g., `apps/hplc_simulator/`); non-app helpers under `chemically/chemically/`; project config under `config/`
- Test files: `test_<module>.py` in `tests/<app_name>/` (e.g., `tests/hplc_simulator/test_engine.py`)
- Migration files: auto-generated `NNNN_description.py` (e.g., `apps/hplc_simulator/migrations/0002_add_check_constraints.py`)
- Templates: `templates/<app_name>/...` — e.g., `templates/chemistry_calculators/calculator/equilibria.html`

**Functions:**
- `snake_case`, imperative verbs: `calculate()`, `process_calculation()`, `generate_chromatogram()`, `add_previous_substances()`
- Private helpers prefixed `_`: `_calculate_ionization_factor()`, `_generate_peak()`, `_calculate_peak_height()`, `_validate_operation_config()` (`apps/hplc_simulator/simulation/engine.py`, `apps/hplc_simulator/models.py`)
- Calculator classes expose a unified `calculate(self, *args, **kwargs)` entry point (`apps/chemistry_calculators/calculations/base.py`)

**Variables:**
- `snake_case`: `missing_prop`, `cleaned_data`, `unit_map`, `result_dict`
- Chemical-science abbreviations are preserved as-is: `pka`, `log_kw`, `s_parameter`, `t_0`, `t_r`, `t_g`, `k_initial` (`apps/hplc_simulator/simulation/engine.py`)
- Single-letter loop/set variables used freely: `k for k in ["c1", "v1", "c2", "v2"]` (`apps/chemistry_calculators/views.py:294`)

**Types:**
- `PascalCase` classes: `MolecularWeightCalculator`, `ReactionBalancer`, `AnalyteProperties`, `UserScore`, `SimulationRequestSerializer`
- Django class-based views named `<Purpose><Kind>`: `CalculateMolecularWeightView`, `LevelListView`, `SimulateView`, `SimulatorIndexView`
- Dataclasses for pure-data structures: `AnalyteProperties`, `ColumnConfig`, `MobilePhaseConfig`, `OperationConfig`, `PeakInfo`, `SimulationResult`, `ScoreBreakdown` (`apps/hplc_simulator/simulation/engine.py`, `apps/hplc_simulator/simulation/scoring.py`)
- Test classes grouped by concern: `CalculatorTests`, `FormTests`, `ViewTests`, `TestRetentionFactorIsocratic`, `TestUserScoreConstraints`

**Constants:**
- `UPPER_CASE` module-level: `CONCENTRATION_CHOICES`, `VOLUME_CHOICES` (`apps/chemistry_calculators/forms.py`), `CALCULATION_REGISTRY` (`apps/chemistry_calculators/calculations/base.py`), `DIFFICULTY_CHOICES`, `COLUMN_CHEMISTRIES` (`apps/hplc_simulator/models.py`)

## Code Style

**Formatting:**
- Flake8 (config in `.flake8`): `max-line-length = 88`, `extend-ignore = E203, W503` (Black-compatible); excludes `.git,__pycache__,*/migrations/*,.agents,_bmad`
- No ruff, black, isort, or mypy config present
- Lint command: `flake8 .` (runs in CI lint job, `.github/workflows/deploy.yml`)

**Linting:**
- Flake8 7.0.0 (pinned in `requirements/development.txt`)
- `# noqa` comments used where star-imports make names undefined: `from .base import *  # noqa: F403`, `SECRET_KEY = os.environ.get(...)  # noqa: F405` (`config/settings/development.py`)

**Quoting — IMPORTANT (mixed conventions by app):**
- `chemistry_calculators` app uses **double quotes**: `label="Chemical Formula"`, `"mol/L"`, `'...'` only for JSON keys inside dicts (`apps/chemistry_calculators/forms.py`)
- `hplc_simulator` app uses **single quotes**: `'C18'`, `'beginner'`, `flow_rate_ml_min='...'` (`apps/hplc_simulator/models.py`, `views.py`, `serializers.py`, `tests/hplc_simulator/*`)
- `config/settings/base.py` is mixed: `INSTALLED_APPS` uses single quotes, `ALLOWED_HOSTS`/`DATABASES`/`LOGGING` use double quotes
- New code: match the quoting style of the file/app you are editing (double quotes in chemistry_calculators, single quotes in hplc_simulator)

## Import Organization

**Order:**
1. Stdlib (logging, json, re, math, abc, dataclasses, typing)
2. Third-party (django, rest_framework, chempy, pint, numpy, scipy)
3. Local relative imports (`from .models import ...`, `from .calculations.base import ...`)

**Not fully consistent:** `apps/hplc_simulator/simulation/engine.py:12-16` mixes stdlib/third-party (`math`, `numpy`, `scipy`, `dataclasses`, `typing`) without blank-line grouping. Prefer grouped ordering: stdlib block, blank line, third-party block, blank line, local block.

**Path Aliases:**
- No `sys.path` aliases in source. `config/settings/base.py:24-25` inserts `apps/` and `chemically/` onto `sys.path`, so imports are written as top-level package names: `from hplc_simulator.models import Level`, `from chemistry_calculators.calculations.base import ReactionBalancer`
- Within a package, relative imports are used: `from .calculations.base import CalculationBase` (`apps/chemistry_calculators/calculations/equilibria.py:10`), `from .models import Level` (`apps/hplc_simulator/views.py:11`)
- Lazy import inside function to avoid circular imports: `from .calculations.units import Q_` inside `SolutionForm.clean()` and `EquilibriumSystemForm.clean()` (`apps/chemistry_calculators/forms.py:222, 430`); `CALCULATION_REGISTRY` uses string `"EquilibriaCalculator"` to avoid circular import (`apps/chemistry_calculators/calculations/base.py:185`)

## Type Hints

- Type hints on all public signatures; `typing` module imported explicitly (`Optional`, `Dict`, `List`, `Tuple`, `Union`, `Any`, `Iterable`): `def calculate(self, substance_or_formula: Union[str, Substance]) -> Optional[float]:` (`apps/chemistry_calculators/calculations/base.py:36-38`)
- Old-style generic annotations (`Dict[str, Any]`, `List[str]`) are used rather than PEP 585 builtins (`dict[str, Any]`) — follow the existing style
- Dataclass fields annotated: `name: str`, `log_kw: float`, `pka: Optional[float] = None` (`apps/hplc_simulator/simulation/engine.py:20-28`)
- `form_class` forward-reference in abstract view: `def form_valid(self, form: form_class) -> HttpResponse:` (`apps/chemistry_calculators/views.py:73`)

## Error Handling

**Patterns:**
- **Calculator layer returns `None` on failure** (silent): `except Exception: return None` (`apps/chemistry_calculators/calculations/base.py:46-53, 65-72`)
- **Calculator layer returns structured failure dict**: `{"success": False, "error": str(e), ...}` with `logger.exception("Equilibria calculation failed")` (`apps/chemistry_calculators/calculations/equilibria.py:129-137`)
- **View layer logs + user message**: broad `except Exception:` → `logging.exception("Dilution calculation failed:")` + `messages.error(self.request, "...")` → `return None` (`apps/chemistry_calculators/views.py:360-367`)
- **API layer returns HTTP status with error dict**: `except Exception as e: logger.exception("Simulation error"); return Response({'error': 'Simulation failed', 'detail': str(e)}, status=500)` (`apps/hplc_simulator/views.py:109-114`); serializer errors → `Response({'errors': serializer.errors}, status=400)` (`apps/hplc_simulator/views.py:56-60`)
- **Form validation**: `raise forms.ValidationError(...)` from `clean()`/`clean_<field>()`, or `self.add_error(field, msg)` for field-targeted errors (`apps/chemistry_calculators/forms.py`, `apps/hplc_simulator/forms.py`)
- **Model validation**: `Model.clean()` collects an `errors = {}` dict across private `_validate_*` helpers, then `raise ValidationError(errors)` (`apps/hplc_simulator/models.py:168-191`)
- **DRF serializer validation**: `validate()`/`validate_<field>()` raising `serializers.ValidationError` (`apps/hplc_simulator/serializers.py:57-140`)
- `pytest.raises(IntegrityError)` relies on DB CHECK constraints as a secondary guard (`tests/hplc_simulator/test_models.py`)
- **Avoid in new code:** bare `except Exception` in calculators is a known loss of diagnostics — log before swallowing when adding new code

## Logging

**Framework:** stdlib `logging` (no structlog/sentry)

**Patterns:**
- Module-level logger: `logger = logging.getLogger(__name__)` (`apps/hplc_simulator/views.py:30`, `apps/chemistry_calculators/calculations/equilibria.py:12`)
- Or direct `logging.exception(...)` without a module logger: `apps/chemistry_calculators/views.py:111, 211, 361`
- Prefer the `logger = logging.getLogger(__name__)` form in new code so messages carry the module name
- Console handler with verbose formatter configured in `config/settings/base.py:173-192`; `LoggingConfigTests.test_logging_console_handler_configured` asserts this config (`tests/chemistry_calculators/test_webapp.py:603-608`)

## Comments

**When to Comment:**
- Module docstrings on every module (`apps/chemistry_calculators/calculations/base.py:1`, `apps/hplc_simulator/simulation/engine.py:1-10`)
- Section divider comments with `# --- Name ---` style: `# --- Calculation Base ---`, `# --- Molecular Weight Calculator ---` (`apps/chemistry_calculators/calculations/base.py:12, 28`)
- Explanatory inline comments for non-obvious science: `# Calculate the missing value using Pint (units handled automatically)` (`apps/chemistry_calculators/calculations/base.py:145`)
- Form field `help_text` doubles as documentation for every field
- **No TODO/FIXME/HACK/XXX markers exist anywhere in `apps/`, `config/`, `chemically/`, or `tests/`** (verified by grep)

**Docstrings:**
- Google-style with `Args:` / `Returns:` / `Raises:` sections is dominant (`apps/chemistry_calculators/calculations/equilibria.py:64-90`, `apps/hplc_simulator/simulation/engine.py:91-110`)
- Older Sphinx-style with `:class:` / `:meth:` cross-references still present in `apps/chemistry_calculators/views.py:161-172`
- Test classes/methods use short docstrings explaining intent: `"""Pressure cannot be negative."""` (`tests/hplc_simulator/test_models.py:47`)
- No `>>>` doctests executed by the test suite (examples in `apps/chemistry_calculators/forms.py` docstrings are illustrative only)

## Function Design

**Size:** Functions are typically small (< 60 lines). Exceptions: `CalculateDilutionView.process_calculation` (~90 lines, `apps/chemistry_calculators/views.py:265-367`) and `SimulateView.post` (~100 lines, `apps/hplc_simulator/views.py:54-161`).

**Parameters:** Named keyword params with defaults for optional science inputs — `def calculate(self, c1, c1_unit, v1, v1_unit, c2, c2_unit, v2, v2_unit, molecular_weight=None, ...)` (`apps/chemistry_calculators/calculations/base.py:112-125`)

**Return Values:**
- Calculators: scalar (`float`/`None`), tuple `(reactants, products)`, or dict with `success` key
- Views: `dict` result merged into context, or `None` on failure
- Engine functions: `float` values or `SimulationResult` dataclass; scoring returns `Tuple[float, bool]` (`apps/hplc_simulator/simulation/engine.py:556`)

## Module Design

**Exports:**
- Implicit (no `__all__` in most modules); `chemically/chemically/context_processors.py:4` declares `__all__ = ["previous_substances", "current_url"]`
- Dataclasses + module-level functions exported from `engine.py`; consumed via `from .simulation.engine import (...)` with parenthesized multi-import (`apps/hplc_simulator/views.py:21-28`)

**Barrel Files:** Not used. Imports name symbols directly.

## Django-Specific Conventions

- **Views:** Class-based only. `TemplateView` for pages, `FormView` for calculators, `APIView` for the HPLC API (`apps/hplc_simulator/views.py`). `permission_classes = [AllowAny]` on every API view — the app has no authentication.
- **Forms:** Field `help_text` and `label` always set; `initial` values on ChoiceField/FloatField; `clean()` calls `super().clean()` first and returns `cleaned_data`; cross-field logic raises `forms.ValidationError` with a full user-facing sentence.
- **Models:** `Meta` class with `ordering`, `indexes`, `constraints` (CheckConstraints for non-negative physics), `unique_together`; `__str__` always defined; JSONField for nested config dicts (`mobile_phase`, `column_config`, `operation_config`); DB CHECK constraints mirrored by `clean()` validation.
- **Serializers:** `ModelSerializer` for reads (with `SerializerMethodField`/`source=` for derived fields), plain `Serializer` for request validation; `min_value`/`max_value` set inline on FloatFields.
- **Settings:** Split `config/settings/{base,development,production}.py`; `base.py` has a dummy `DATABASES` placeholder so it imports standalone (`config/settings/base.py:99-103`); development/production override via star-import.
- **Management commands:** Subclass `BaseCommand`, use `self.stdout.write` and `self.style.SUCCESS`, idempotent via `update_or_create`/`get_or_create` (`apps/hplc_simulator/management/commands/seed_hplc_data.py`).
- **Migrations:** Generated with Django 5.2; CheckConstraints added via `migrations.AddConstraint` (`apps/hplc_simulator/migrations/0002_add_check_constraints.py`).
- **URLs:** Named routes used with `reverse()` in views and tests (`reverse("molecular_weight")`); two app-level `urls.py` included from `config/urls.py` under `''` and `'hplc/'`.

## Session/State Conventions

- Session-based state, no Redis/Celery: `request.session["previous_substances"]` list, deduped by `add_previous_substances()` (`apps/chemistry_calculators/utils/__init__.py:7-13`)
- Session key created on demand in API views: `if not session_key: request.session.create()` (`apps/hplc_simulator/views.py:179-181`)
- Context processor injects `previous_substances` into every template (`chemically/chemically/context_processors.py`)

---

*Convention analysis: 2026-08-05*
