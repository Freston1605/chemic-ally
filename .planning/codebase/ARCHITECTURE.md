<!-- refreshed: 2026-08-05 -->
# Architecture

**Analysis Date:** 2026-08-05

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        HTTP Entry Points                             │
│   manage.py runserver        config/asgi.py (Mangum → Lambda)        │
│   `manage.py`                `config/asgi.py`                        │
└───────────────┬──────────────────────────────┬──────────────────────┘
                │                              │
                ▼                              ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│  URL routing (config/urls.py) │  │                                  │
│  ''      → chemistry_calculators.urls   '/hplc/' → hplc_simulator.urls
└───────────────┬───────────────┘  └───────────────┬──────────────────┘
                │                                  │
                ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       View Layer (apps/)                             │
│  chemistry_calculators/views.py          hplc_simulator/views.py     │
│  CBV FormView/TemplateView               TemplateViews + DRF APIViews│
└───────────────┬──────────────────────────────────┬──────────────────┘
                │                                  │
                ▼                                  ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│  Validation layer             │  │  Validation layer                 │
│  forms.py (Django Forms)      │  │  serializers.py (DRF)             │
│  `apps/chemistry_calculators/ │  │  `apps/hplc_simulator/serializers.py`
│   forms.py`                   │  │  model.clean() for UserScore      │
└───────────────┬───────────────┘  └───────────────┬──────────────────┘
                │                                  │
                ▼                                  ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│  Domain engine                │  │  Domain engine                    │
│  calculations/ (chempy/Pint)  │  │  simulation/ (numpy/scipy, LSS)   │
│  `apps/chemistry_calculators/ │  │  `apps/hplc_simulator/simulation/ │
│   calculations/`              │  │   engine.py, scoring.py`          │
└───────────────┬───────────────┘  └───────────────┬──────────────────┘
                │                                  │
                ▼                                  ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│  State / Persistence          │  │  State / Persistence              │
│  Django session only          │  │  PostgreSQL (models.py) + session │
│  (no DB models)               │  │  `apps/hplc_simulator/models.py`  │
└───────────────────────────────┘  └──────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Project config | Settings (base/dev/prod), root URLconf, ASGI/WSGI, storage backends | `config/` |
| chemistry_calculators | Server-rendered chemistry calculators (MW, reaction balancing, dilution, equilibria) | `apps/chemistry_calculators/` |
| calculations engine | ChemPy/Pint-backed pure calculation classes | `apps/chemistry_calculators/calculations/` |
| hplc_simulator | HPLC chromatography simulator: levels, simulation API, scores, progress | `apps/hplc_simulator/` |
| simulation engine | LSS retention model, van Deemter, EMG peaks, pressure, scoring | `apps/hplc_simulator/simulation/` |
| chemically package | Template context processors (previous substances, active nav) | `chemically/chemically/context_processors.py` |
| Templates | Project-level Django template dir | `templates/` |
| Static assets | App-scoped CSS/JS | `static/` |
| Tests | pytest-django suites, one dir per app | `tests/` |

## Pattern Overview

**Overall:** Two-app Django monolith: classic server-rendered MVC (chemistry_calculators) beside a DRF JSON API (hplc_simulator) with a pure-Python simulation core. Stateless serverless deployment (Lambda + Mangum), with session-based user identity (no auth accounts).

**Key Characteristics:**
- Class-based views everywhere (FormView/TemplateView for the webapp, DRF `APIView` for the API)
- Pure calculation/simulation logic decoupled from HTTP in `calculations/` and `simulation/` packages — testable without Django request machinery
- `config/settings/base.py` mutates `sys.path` (`apps/` and `chemically/`) so apps import as top-level packages (`chemistry_calculators`, `hplc_simulator`)
- Session (not users) is the identity axis: `session_key` foreign keys on `UserScore` and `LevelProgress`, and `previous_substances` list in the session for the webapp
- Settings split by environment with a hard-failing production module (`config/settings/production.py` raises `ImproperlyConfigured` on missing env vars)

## Layers

**Config / Infrastructure:**
- Purpose: Settings, URL routing, ASGI handler, storage backends
- Location: `config/`
- Contains: `settings/base.py`, `settings/development.py`, `settings/production.py`, `urls.py`, `asgi.py`, `wsgi.py`, `storage_backends.py`
- Depends on: `python-dotenv`, `django-storages`, `mangum`
- Used by: everything (settings), Lambda (`asgi.py` handler)

**View Layer (HTTP):**
- Purpose: Request handling, form/serializer binding, rendering
- Location: `apps/chemistry_calculators/views.py`, `apps/hplc_simulator/views.py`
- Contains: CBVs (`BaseCalculateView` and subclasses, `LandingPage`, `SimulatorIndexView`, `SimulatorDetailView`), DRF APIViews (`LevelListView`, `LevelDetailView`, `SimulateView`, `ScoreSubmissionView`, `UserScoresView`, `LevelProgressView`)
- Depends on: forms/serializers, domain engines, models
- Used by: URLconfs

**Validation Layer:**
- Purpose: Input parsing, cleaning, and cross-field rules
- Location: `apps/chemistry_calculators/forms.py`, `apps/hplc_simulator/forms.py`, `apps/hplc_simulator/serializers.py`, model `clean()` in `apps/hplc_simulator/models.py`
- Contains: `MolecularFormulaForm`, `ChemicalReactionForm`, `EquilibriumSystemForm` (JSON hidden fields), `SolutionForm`; DRF `SimulationRequestSerializer` tree (`MobilePhaseSerializer`, `ColumnConfigSerializer`, `OperationConfigSerializer`), `ScoreSubmissionSerializer`, `UserScoreSerializer`, `LevelProgressSerializer`
- Depends on: Pint via `calculations/units.py` (in form `clean()`)
- Used by: views

**Domain Engine (calculations/simulation):**
- Purpose: Pure domain math, no Django imports
- Location: `apps/chemistry_calculators/calculations/` (`base.py`, `equilibria.py`, `units.py`), `apps/hplc_simulator/simulation/` (`engine.py`, `scoring.py`)
- Contains: `CalculationBase` + `MolecularWeightCalculator`, `ReactionBalancer`, `DilutionCalculator`, `EquilibriaCalculator`; `AnalyteProperties`/`ColumnConfig`/`MobilePhaseConfig`/`OperationConfig`/`PeakInfo`/`SimulationResult` dataclasses + `generate_chromatogram()`, `calculate_score()`, `evaluate_run()`
- Depends on: chempy, pint, numpy, scipy
- Used by: views
- Constraint: `apps/hplc_simulator/SCIENTIFIC_LOGIC.md` documents physical invariants the engine must respect

**Persistence Layer:**
- Purpose: Model storage
- Location: `apps/hplc_simulator/models.py`
- Contains: `Analyte`, `Level`, `UserScore`, `LevelProgress` (the only real DB models in the project; `chemistry_calculators/models.py` is empty — it stores state in the session instead)
- Depends on: Django ORM, PostgreSQL in production / SQLite in development
- Used by: views, seed command

**Presentation Layer:**
- Purpose: Server-rendered HTML and client-side JS
- Location: `templates/` (project `TEMPLATES.DIRS`), `static/`
- Contains: `templates/chemistry_calculators/` (base.html, landing.html, calculator/*, components/*), `templates/hplc_simulator/` (hplc_base.html, index.html, simulator.html), `static/css/style.css`, `static/js/*.js`, `static/hplc_simulator/{css,js}/simulator.*`
- Depends on: context processors in `chemically/chemically/context_processors.py`, Bootstrap 5.3, MathJax 3 (webapp), Alpine.js + Plotly (HPLC simulator page)

## Data Flow

### Primary Request Path — Chemistry Calculator (server-rendered)

1. Browser GET/POST → `config/urls.py` → `apps/chemistry_calculators/urls.py` route (e.g. `calculate/dilution`) (`config/urls.py:22`, `apps/chemistry_calculators/urls.py:11-31`)
2. `BaseCalculateView` (FormView subclass) binds the form; on POST `form_valid()` calls the concrete `process_calculation()` (`apps/chemistry_calculators/views.py:73-84`)
3. The view constructs a calculator from the `calculations/` package and calls `.calculate()` — ChemPy/Pint do the work (e.g. `DilutionCalculator.calculate`, `apps/chemistry_calculators/calculations/base.py:112`)
4. On success the view records substances to the session via `add_previous_substances()` (`apps/chemistry_calculators/utils/__init__.py:7`)
5. `form_valid` renders the same template with the result dict in context; MathJax renders LaTeX results client-side
6. `chemically.context_processors.previous_substances` injects session history into every template render (`chemically/chemically/context_processors.py:7`)

### Primary Request Path — HPLC Simulation API (JSON)

1. JS client POSTs to `/hplc/api/simulate/` (`apps/hplc_simulator/urls.py:19`) → `SimulateView.post()` (`apps/hplc_simulator/views.py:51`)
2. `SimulationRequestSerializer` validates and cross-validates (pH vs column chemistry, HILIC/NP rejection) (`apps/hplc_simulator/serializers.py:121-140`)
3. View loads `Level` + related `Analyte` rows and maps them into engine dataclasses (`AnalyteProperties`, `ColumnConfig`, `MobilePhaseConfig`, `OperationConfig`) (`apps/hplc_simulator/views.py:66-99`)
4. `generate_chromatogram()` runs LSS retention + van Deemter plate count + EMG peaks + Kozeny-Carman pressure (`apps/hplc_simulator/simulation/engine.py:348`); early-returns an overpressure result if pressure exceeds `max_pressure_bar`
5. `calculate_score()` computes score/success (`apps/hplc_simulator/simulation/engine.py:549`)
6. Response is validated by `SimulationResponseSerializer` and returned as JSON (`apps/hplc_simulator/views.py:128-161`)

### Score Persistence Flow

1. Client POSTs run results to `/hplc/api/scores/` → `ScoreSubmissionView.post()` (`apps/hplc_simulator/views.py:164`)
2. `ScoreSubmissionSerializer` validates; view ensures a session key exists (`request.session.create()`) (`apps/hplc_simulator/views.py:178-181`)
3. `UserScore` row saved after `full_clean()` (model-level check constraints + `clean()` cross-field validation) (`apps/hplc_simulator/models.py:168-214`)
4. `LevelProgress` upserted via `get_or_create` then incrementally updated (best score / best resolution / best run time / attempts / completed) (`apps/hplc_simulator/views.py:205-230`)
5. History endpoints `/hplc/api/scores/history/` and `/hplc/api/progress/` read back by `session_key` (`apps/hplc_simulator/views.py:238-263`)

**State Management:**
- Webapp: Django session only — `previous_substances` list + per-form state; no DB writes
- HPLC: session `session_key` is the user identity for `UserScore`/`LevelProgress`; `Level`/`Analyte` are seeded static content
- No Celery, no Redis, no cache framework in use

## Key Abstractions

**CalculationBase:**
- Purpose: Contract for every chemistry calculation — `description`, `input_spec`, `output_spec` metadata + `calculate()` method
- Examples: `apps/chemistry_calculators/calculations/base.py:13` (`MolecularWeightCalculator`, `ReactionBalancer`, `DilutionCalculator`), `apps/chemistry_calculators/calculations/equilibria.py:15` (`EquilibriaCalculator`)
- Pattern: Template method — views call `.calculate()`, never internals

**BaseCalculateView:**
- Purpose: Abstract FormView that turns any form submission into a rendered result via the abstract `process_calculation(form) -> dict`
- Examples: `apps/chemistry_calculators/views.py:37` and subclasses at lines 99, 161, 219, 370
- Pattern: Abstract base class (ABC) with `template_name`/`form_class`/`success_url` set by subclasses

**Engine config dataclasses:**
- Purpose: Typed, unit-clear parameter bundles passed into the pure simulation functions
- Examples: `AnalyteProperties`, `ColumnConfig`, `MobilePhaseConfig`, `OperationConfig`, `PeakInfo`, `SimulationResult` in `apps/hplc_simulator/simulation/engine.py:19-81`
- Pattern: Immutable dataclasses as the boundary between ORM rows and the simulation engine (view maps model → dataclass)

**DRF serializer tree:**
- Purpose: Nested request/response contracts for the simulation API, mirroring the engine config dataclasses
- Examples: `SimulationRequestSerializer` → `MobilePhaseSerializer`/`ColumnConfigSerializer`/`OperationConfigSerializer`; `SimulationResponseSerializer` → `PeakSerializer`/`MetricsSerializer` in `apps/hplc_simulator/serializers.py`
- Pattern: `Serializer` (not ModelSerializer) for the simulation contract; ModelSerializer for Level/Score reads

## Entry Points

**manage.py (development/ops):**
- Location: `manage.py`
- Triggers: CLI (`python manage.py runserver/migrate/seed_hplc_data`)
- Responsibilities: Defaults `DJANGO_SETTINGS_MODULE` to `config.settings.development`

**config/asgi.py (production Lambda):**
- Location: `config/asgi.py`
- Triggers: AWS Lambda function URL / CloudFront via Mangum
- Responsibilities: `application = get_asgi_application()`, `handler = Mangum(application, lifespan="off")`; defaults to `config.settings.production`; infra wired in `template.yaml`

**config/urls.py (routing hub):**
- Location: `config/urls.py`
- Triggers: Every request
- Responsibilities: `/admin/`, root → `chemistry_calculators.urls`, `/hplc/` → `hplc_simulator.urls`

**config/wsgi.py (legacy):**
- Location: `config/wsgi.py`
- Triggers: WSGI servers only (EB-era deploy remnants — `.ebextensions/`, `.elasticbeanstalk/`, `.platform/` still present in repo)
- Responsibilities: Standard Django WSGI app, defaults to development settings

**Management command:**
- Location: `apps/hplc_simulator/management/commands/seed_hplc_data.py`
- Triggers: `python manage.py seed_hplc_data` (required after migrate)
- Responsibilities: Seeds `Analyte` and `Level` rows for the simulator

## Architectural Constraints

- **Threading:** Single-threaded Django request/response; serverless Lambda (1024 MB, 30s timeout per `template.yaml`). No worker threads or background jobs.
- **Global state:** One module-level singleton: the Pint `UnitRegistry` (`ureg`) in `apps/chemistry_calculators/calculations/units.py:12`, shared across forms and calculators. `np.random.normal` in `generate_chromatogram` (`apps/hplc_simulator/simulation/engine.py:418`) is the only nondeterminism in the engine.
- **DB connections:** `CONN_MAX_AGE=0` in `config/settings/production.py:77` — required for Lambda (no connection reuse across warm containers).
- **Settings:** Production settings hard-fail on missing env vars (`_required_env`, `config/settings/production.py:19`). `SECRET_KEY` required in production; dev-only fallback in `base.py:32`.
- **Session identity:** All user-visible persistence keys off `session_key`; there is no auth model. Enabling accounts later requires migrating `UserScore`/`LevelProgress` away from `session_key`.
- **Physical constraints:** `apps/hplc_simulator/SCIENTIFIC_LOGIC.md` is authoritative for the simulation engine — changes must not violate documented invariants.
- **Simulator scope:** Only reversed-phase retention (C18/C8/C4/Phenyl) is implemented; HILIC and NP are rejected in both `ColumnConfigSerializer.validate` (`apps/hplc_simulator/serializers.py:73-78`) and `UserScore.clean` (`apps/hplc_simulator/models.py:207-214`).
- **sys.path layout:** `config/settings/base.py:24-25` inserts `apps/` and `chemically/` into `sys.path`; apps must be imported as `chemistry_calculators.*` / `hplc_simulator.*`, and the context-processor package resolves as `chemically.context_processors` from `chemically/chemically/`.

## Anti-Patterns

### Swallowed exceptions in reaction balancing

**What happens:** `BalanceChemicalReaction.process_calculation` wraps everything in `try/except Exception` and `return`s `None` (the annotated return type is `str`) (`apps/chemistry_calculators/views.py:193-216`). `form_valid` then renders with `result=None`.
**Why it's wrong:** The template receives `None` as `result`; a crash in balancing silently degrades to an error-message page with a log line. Type annotation and actual return diverge.
**Do this instead:** Return an empty dict / structured error payload (as `CalculateDilutionView` does at `apps/chemistry_calculators/views.py:317`), or narrow the exception scope.

### Duplicated scoring implementations

**What happens:** The scoring formula exists twice: `calculate_score()` in `apps/hplc_simulator/simulation/engine.py:549` (used by `SimulateView`) and `evaluate_run()` in `apps/hplc_simulator/simulation/scoring.py:25` (unused by any view).
**Why it's wrong:** Two copies of the same business formula will drift; `scoring.py` adds a `ScoreBreakdown` dataclass and messaging that the engine path duplicates ad hoc in `SimulateView` (`apps/hplc_simulator/views.py:152-155`).
**Do this instead:** Keep one scoring module (either `scoring.py` or engine) and have `SimulateView` call it; the engine should return raw metrics only.

### Incorrect Meta.model in LevelProgressSerializer

**What happens:** `LevelProgressSerializer` declares `model = UserScore` while listing LevelProgress-only fields (`best_score`, `best_resolution`, `best_run_time`, `attempts`, `completed`, `last_attempt_at`) (`apps/hplc_simulator/serializers.py:199-209`). `LevelProgress` is not even imported (`apps/hplc_simulator/serializers.py:2`).
**Why it's wrong:** DRF resolves `fields` against `UserScore` at instantiation — `LevelProgressView` (`apps/hplc_simulator/views.py:253`) will raise at runtime for `best_score` on `UserScore`. Likely copy-paste defect from `UserScoreSerializer`.
**Do this instead:** Set `model = LevelProgress` and add it to the import list.

### Bare `except Exception` with `pass` in molecular weight loop

**What happens:** `MolecularWeightCalculator.calculate` catches all exceptions and returns `None` (`apps/chemistry_calculators/calculations/base.py:52-53`); the view filters `None` results silently (`apps/chemistry_calculators/views.py:152-154`).
**Why it's wrong:** A formula that fails to parse vanishes from the results with no feedback about which input was invalid.
**Do this instead:** Return an error marker per molecule or collect per-item errors so the template can highlight bad inputs.

## Error Handling

**Strategy:** Fail-loud in production config (`ImproperlyConfigured` on missing env vars); fail-gentle in the webapp (Django `messages.error` + log via `logging.exception`); explicit 400/500 JSON responses in the API.

**Patterns:**
- Webapp: `logging.exception(...)` + `messages.error(request, ...)` in each `process_calculation` catch block (`apps/chemistry_calculators/views.py:210-216, 360-367`)
- API validation: `Response({'errors': serializer.errors}, status=400)` (`apps/hplc_simulator/views.py:56-59`)
- API engine failure: `500` with `{'error': ..., 'detail': str(e)}` after `logger.exception` (`apps/hplc_simulator/views.py:109-114`)
- Model-level: `full_clean()` before save in `ScoreSubmissionView` (`apps/hplc_simulator/views.py:197`), returning `e.message_dict` as 400
- Engine-level: `generate_chromatogram` returns an overpressure `SimulationResult` instead of raising (`apps/hplc_simulator/simulation/engine.py:375-384`); `EquilibriaCalculator` returns `{'success': False, 'error': ...}` (`apps/chemistry_calculators/calculations/equilibria.py:129-137`)

## Cross-Cutting Concerns

**Logging:** Console handler with verbose formatter configured in `config/settings/base.py:173`; production adds `django.request`/`django.security` WARNING loggers (`config/settings/production.py:120`). Output to stdout/stderr for CloudWatch.
**Validation:** Two parallel systems — Django Forms (webapp) and DRF serializers (API), plus model `clean()`/`CheckConstraint` on `UserScore`/`LevelProgress` (`apps/hplc_simulator/models.py:146-163, 239-252`).
**Authentication:** None. `AllowAny` on all API views (`apps/hplc_simulator/views.py:34, 44, 52, 165, 239, 254`); identity via `session_key`.
**Storage:** S3 via `config/storage_backends.py` in production (`StaticStorage` public + long cache; `MediaStorage` private signed URLs), filesystem in development (`config/settings/development.py:44-51`).

---

*Architecture analysis: 2026-08-05*
