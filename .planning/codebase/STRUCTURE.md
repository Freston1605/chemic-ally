# Codebase Structure

**Analysis Date:** 2026-08-05

## Directory Layout

```
chemic-ally/
├── manage.py                  # CLI entry; defaults to config.settings.development
├── config/                    # Django project config
│   ├── settings/
│   │   ├── base.py            # Shared settings; sys.path wiring; loads .env
│   │   ├── development.py     # SQLite, DEBUG=True, local file storage
│   │   └── production.py      # PostgreSQL/RDS, S3, strict HTTPS, Lambda-ready
│   ├── urls.py                # Root URLconf (admin, '', /hplc/)
│   ├── asgi.py                # ASGI app + Mangum handler (Lambda entry point)
│   ├── wsgi.py                # WSGI app (legacy EB-era entry point)
│   └── storage_backends.py    # StaticStorage (public) + MediaStorage (signed URLs)
├── apps/                      # Project apps (added to sys.path by base.py)
│   ├── chemistry_calculators/ # Server-rendered chemistry calculators (no DB models)
│   │   ├── views.py           # CBVs incl. BaseCalculateView ABC
│   │   ├── forms.py           # Django Forms (MolecularFormulaForm, SolutionForm, ...)
│   │   ├── urls.py            # Calculator routes
│   │   ├── models.py          # Empty (state lives in the session)
│   │   ├── calculations/      # Pure engine: base.py, equilibria.py, units.py
│   │   └── utils/             # add_previous_substances session helper
│   └── hplc_simulator/        # HPLC simulator: real models + DRF API + sim engine
│       ├── models.py          # Analyte, Level, UserScore, LevelProgress
│       ├── serializers.py     # DRF request/response contracts
│       ├── views.py           # TemplateViews + 6 APIViews
│       ├── forms.py           # SimulationParameterForm (web UI parity form)
│       ├── urls.py            # /hplc/ page + /hplc/api/* routes
│       ├── simulation/        # engine.py (LSS/van Deemter/EMG), scoring.py
│       ├── migrations/        # 0001_initial, 0002_check_constraints, 0003_analyte_response
│       ├── management/commands/seed_hplc_data.py
│       └── SCIENTIFIC_LOGIC.md  # Physical invariants — engine changes must respect
├── chemically/
│   └── chemically/            # Non-app package; context_processors.py
├── templates/                 # Project-level TEMPLATES.DIRS
│   ├── chemistry_calculators/ # base.html, landing.html, calculator/*, components/*
│   └── hplc_simulator/        # hplc_base.html, index.html, simulator.html
├── static/                    # STATICFILES_DIRS
│   ├── css/style.css
│   ├── js/                    # calculators.js, equilibria.js, substance_input.js, ...
│   └── hplc_simulator/        # css/simulator.css, js/simulator.js
├── tests/                     # pytest-django suites (one dir per app)
│   ├── chemistry_calculators/test_webapp.py
│   └── hplc_simulator/        # test_engine.py, test_models.py, test_validation.py
├── requirements/              # base.txt, development.txt, production.txt, requirements-lambda.txt
├── .github/workflows/deploy.yml  # CI/CD: lint → test → security → Docker → SAM deploy
├── template.yaml              # SAM infra: Lambda + Function URL + CloudFront + ACM + R53
├── OPS_PLAYBOOK.md            # Infrastructure runbook (resources, secrets, DR)
├── pytest.ini                 # DJANGO_SETTINGS_MODULE=config.settings.development
├── Dockerfile                 # Lambda container image
├── db.sqlite3                 # Dev SQLite DB (local artifact)
├── media/                     # Dev media root
├── staticfiles/               # collectstatic output (generated)
├── .ebextensions/ .elasticbeanstalk/ .platform/   # Legacy EB deploy artifacts
├── .env.example               # Env var template (SECRET_KEY, RDS_*, AWS_*)
└── .env                       # Present — local env config, never read/committed
```

## Directory Purposes

**`config/`:**
- Purpose: Everything that wires the Django project together — settings, routing, ASGI/WSGI entry, storage
- Contains: Settings split base/dev/prod; `asgi.py` exports both `application` and the Mangum `handler`
- Key files: `config/settings/base.py` (sys.path + shared config), `config/settings/production.py` (hard-failing required env), `config/storage_backends.py`

**`apps/chemistry_calculators/`:**
- Purpose: The original server-rendered calculator webapp (molecular weight, reaction balancing, dilution, equilibria)
- Contains: CBVs, forms, per-calculator pure logic in `calculations/`, session helper in `utils/`
- Key files: `views.py` (BaseCalculateView ABC + 4 concrete views), `calculations/base.py` (CalculationBase + registry), `calculations/units.py` (Pint `ureg` singleton)

**`apps/hplc_simulator/`:**
- Purpose: HPLC chromatography simulator — seeded levels/analytes, simulation API, score/progress tracking
- Contains: ORM models, DRF serializers, API + template views, numpy/scipy simulation engine, seed command
- Key files: `simulation/engine.py` (the scientific core), `models.py`, `views.py`, `serializers.py`

**`chemically/`:**
- Purpose: Non-app helper package for template context (`previous_substances`, `current_url`)
- Contains: `chemically/context_processors.py` (note the double-nested `chemically/chemically/` layout)
- Key files: `chemically/chemically/context_processors.py` — registered in `config/settings/base.py:85-86`

**`templates/`:**
- Purpose: Project-level template dir (fallback for APP_DIRS), one subdir per app
- Contains: `chemistry_calculators/` (base.html, landing, calculator/ partials, components/_result_card.html), `hplc_simulator/` (hplc_base.html extends chemistry base, index, simulator)

**`static/`:**
- Purpose: App-scoped static assets (collected via `STATICFILES_DIRS` = `BASE_DIR/static`)
- Contains: shared `css/style.css`, shared `js/*.js` (calculators, equilibria, form_validation, substance_input, dark_mode, clipboard), `hplc_simulator/{css,js}/simulator.*`

**`tests/`:**
- Purpose: pytest-django suites; mirrors the app layout rather than living inside apps
- Contains: `chemistry_calculators/test_webapp.py` (608 lines), `hplc_simulator/test_engine.py`, `test_models.py`, `test_validation.py`
- Note: `apps/hplc_simulator/tests/` also exists but is an empty `__init__.py` package — the real suite is at `tests/hplc_simulator/`

## Key File Locations

**Entry Points:**
- `manage.py`: CLI (dev default settings)
- `config/asgi.py`: Lambda handler (`handler = Mangum(application, lifespan="off")`) — production entry
- `config/urls.py`: root routing hub
- `config/wsgi.py`: legacy WSGI entry

**Configuration:**
- `config/settings/base.py`, `config/settings/development.py`, `config/settings/production.py`
- `pytest.ini`: test settings module
- `requirements/base.txt`, `requirements/development.txt`, `requirements/production.txt`
- `template.yaml`: AWS SAM infrastructure (Lambda 1024MB/30s, Function URL, CloudFront, ACM, Route 53)
- `.github/workflows/deploy.yml`: CI/CD pipeline (branch `main`)

**Core Logic:**
- `apps/chemistry_calculators/calculations/base.py`: CalculationBase contract, MW/Reaction/Dilution calculators, `CALCULATION_REGISTRY`
- `apps/chemistry_calculators/calculations/equilibria.py`: EqSystem-based equilibria solver
- `apps/chemistry_calculators/views.py`: webapp request handling
- `apps/hplc_simulator/simulation/engine.py`: LSS retention, gradient elution, van Deemter plate count, EMG peaks, Kozeny-Carman pressure, `generate_chromatogram`, `calculate_score`
- `apps/hplc_simulator/simulation/scoring.py`: parallel scoring implementation (`evaluate_run`) — currently unused by views
- `apps/hplc_simulator/models.py`: Analyte, Level, UserScore, LevelProgress
- `apps/hplc_simulator/serializers.py`: simulation API contracts
- `apps/hplc_simulator/views.py`: DRF APIViews + TemplateViews

**Testing:**
- `tests/chemistry_calculators/test_webapp.py`
- `tests/hplc_simulator/test_engine.py`, `tests/hplc_simulator/test_models.py`, `tests/hplc_simulator/test_validation.py`

## Naming Conventions

**Files:**
- Python: `snake_case.py` throughout (`molecular_weight.html`, `seed_hplc_data.py`)
- Templates: lowercase, snake_case; app name repeated in the path (`chemistry_calculators/calculator/dilution.html`)
- Shared template partials prefixed with `_` (`_calculator_base.html`, `_result_card.html`, `_field_row.html`, `_tag_input_field.html`)
- Tests: `test_<module>.py` under `tests/<app>/` (`test_engine.py`, `test_webapp.py`)

**Directories:**
- Apps live under `apps/` but are imported as top-level packages (`chemistry_calculators`, `hplc_simulator`) via `sys.path` in `config/settings/base.py:24-25`
- Tests mirror app names under `tests/` (`tests/chemistry_calculators/`, `tests/hplc_simulator/`)
- Templates and static both use `<app>/` subdirectories

**Classes/Views:**
- CBVs: `<Action><Domain>View` (`CalculateMolecularWeightView`, `BalanceChemicalReaction`, `LevelListView`)
- Calculators: `<Domain>Calculator` (`MolecularWeightCalculator`, `DilutionCalculator`, `EquilibriaCalculator`)

## Where to Add New Code

**New Feature (chemistry calculator):**
- Engine: `apps/chemistry_calculators/calculations/<name>.py` — subclass `CalculationBase` in `base.py` or a new module; register in `CALCULATION_REGISTRY` (`apps/chemistry_calculators/calculations/base.py:181`)
- Form: `apps/chemistry_calculators/forms.py`
- View: subclass `BaseCalculateView` in `apps/chemistry_calculators/views.py` implementing `process_calculation`
- Route: `apps/chemistry_calculators/urls.py` under `calculate/`
- Template: `templates/chemistry_calculators/calculator/<name>.html` (extend `_calculator_base.html`)
- Tests: `tests/chemistry_calculators/test_webapp.py`

**New Feature (HPLC API endpoint):**
- Serializer: `apps/hplc_simulator/serializers.py`
- View: `apps/hplc_simulator/views.py` (APIView with `permission_classes = [AllowAny]`)
- Route: `apps/hplc_simulator/urls.py` under `api/`
- Tests: `tests/hplc_simulator/test_validation.py` (or a new `test_api.py`)

**New Feature (simulation physics):**
- Implementation: `apps/hplc_simulator/simulation/engine.py` — **must respect `apps/hplc_simulator/SCIENTIFIC_LOGIC.md`**
- Keep `SimulateView` mapping models → engine dataclasses unchanged unless the contract changes; update `SimulationResponseSerializer` accordingly
- Tests: `tests/hplc_simulator/test_engine.py`

**New Model (HPLC domain):**
- Model: `apps/hplc_simulator/models.py` + `makemigrations`
- Serializer: `apps/hplc_simulator/serializers.py`
- Seed data (if needed): `apps/hplc_simulator/management/commands/seed_hplc_data.py`
- Tests: `tests/hplc_simulator/test_models.py`

**Shared template context:**
- Context processor: `chemically/chemically/context_processors.py`, then register in `config/settings/base.py` `context_processors` list

**Utilities:**
- Shared session helpers for the webapp: `apps/chemistry_calculators/utils/__init__.py`
- Project-wide (settings-adjacent) helpers: `config/` modules (e.g. `storage_backends.py`)

## Special Directories

**`.ebextensions/`, `.elasticbeanstalk/`, `.platform/`:**
- Purpose: Legacy AWS Elastic Beanstalk deployment artifacts (superseded by SAM/Lambda in `template.yaml`)
- Generated: No
- Committed: Yes

**`db.sqlite3`:**
- Purpose: Local development SQLite database
- Generated: Yes
- Committed: No

**`media/`:**
- Purpose: Development media root (`MEDIA_ROOT` in `config/settings/development.py:38`)
- Generated: Yes
- Committed: No (contains `.gitkeep`-style placeholder)

**`staticfiles/`:**
- Purpose: `collectstatic` output directory (`STATIC_ROOT`)
- Generated: Yes
- Committed: No

**`.venv/`:**
- Purpose: Local virtual environment
- Generated: Yes
- Committed: No

**`docs/`, `scripts/`:**
- Purpose: Empty placeholders at repo root; no content as of this analysis
- Generated: No
- Committed: Yes

**`_bmad/`, `_bmad-output/`, `.opencode/`, `.serena/`, `.agents/`:**
- Purpose: Tooling/agent configuration and output directories (not application code)
- Generated: Mixed
- Committed: Yes

---

*Structure analysis: 2026-08-05*
