# AGENTS.md — ChemicAlly

## Project

Django 5.2 web app providing chemistry calculators (molecular weight, reaction balancing, dilution, equilibria) and an HPLC chromatography simulator. Deployed on AWS Lambda (container image, Python 3.14) behind CloudFront. Domain: chemic-ally.xyz.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/development.txt
cp .env.example .env          # set SECRET_KEY at minimum
python manage.py migrate      # creates HPLC simulator tables + Django framework tables

# Seed HPLC simulator data (required after first migrate or new migrations)
python manage.py seed_hplc_data

# Dev server (defaults to config.settings.development)
python manage.py runserver

# Lint
flake8 .

# Tests (CI runs both)
pytest -v                      # preferred, covers chemistry_calculators + hplc_simulator
python manage.py test chemistry_calculators   # Django runner, chemistry_calculators only (no HPLC tests)

# CI order: lint → deploy check → pytest → Django tests → security scan → Docker build → SAM deploy
```

## Settings

- `config/settings/base.py` — shared config. Loads `.env` via `python-dotenv`. Adds `rest_framework` and `hplc_simulator` to INSTALLED_APPS.
- `config/settings/development.py` — SQLite, DEBUG=True, local file storage.
- `config/settings/production.py` — PostgreSQL (RDS env vars), S3 storage, strict HTTPS, `CONN_MAX_AGE=0` (Lambda-compatible). **Raises `ImproperlyConfigured** if required env vars are missing. Uses `ALLOWED_HOSTS` from env var.
- `config/asgi.py` — ASGI application with Mangum handler for Lambda (`handler = Mangum(application)`).
- `manage.py` defaults to `config.settings.development`.
- `pytest.ini` also uses `config.settings.development`.
- Lambda sets `DJANGO_SETTINGS_MODULE=config.settings.production`.

## Architecture

```
apps/
  chemistry_calculators/      # Original Django app — chemistry calculators
    calculations/             # Engine: MolecularWeight, Reaction, Dilution, Equilibria
      units.py                # Pint unit helpers (moved here from utils/)
    utils/                    # Session persistence helpers
    forms.py, views.py, urls.py
  hplc_simulator/             # HPLC chromatography simulator (has real DB models)
    models.py                 # Analyte, Level, UserScore, LevelProgress
    simulation/engine.py      # LSS model, gradient elution, van Deemter, EMG peaks
    serializers.py            # DRF serializers
    views.py                  # TemplateViews + DRF APIViews
    management/commands/      # seed_hplc_data
    SCIENTIFIC_LOGIC.md       # Physical constraints — DO NOT violate
    tests/
  chemically/                   # Non-app package on sys.path
    context_processors.py       # Injects previous_substances into templates
config/                       # Django project config
  settings/                   # base.py, development.py, production.py
  storage_backends.py         # StaticStorage (public) + MediaStorage (signed URLs)
  urls.py                     # Includes chemistry_calculators (root) + hplc_simulator (/hplc/)
tests/
  chemistry_calculators/      # chemistry_calculators tests
  hplc_simulator/             # hplc_simulator tests (engine, models, validation)
```

- `base.py` adds `apps/` and `chemically/` to `sys.path` so imports work as `chemistry_calculators` and `hplc_simulator`.
- **HPLC simulator is the first app with real database models.** `python manage.py migrate` is no longer a no-op — it creates Analyte, Level, UserScore, LevelProgress tables.
- DRF is used for the HPLC simulator API (`/hplc/api/*`). Webapp remains server-rendered.
- No Celery, no Redis. App is server-rendered Django with Bootstrap 5.3.

## Key Libraries

| Library | Purpose |
|---------|---------|
| ChemPy  | Chemical formula parsing, reaction balancing, equilibria (EqSystem) |
| Pint    | Unit-safe arithmetic (concentration/volume) |
| MathJax 3 | Browser-side LaTeX rendering |
| django-storages + boto3 | S3 static/media in production |
| DRF (djangorestframework) | HPLC simulator API endpoints |
| numpy + scipy | HPLC simulation engine (chromatogram generation) |

## Deploy (AWS Lambda + CloudFront)

- **Compute:** Lambda (container image, 1024MB, 30s timeout, Python 3.14) via Mangum ASGI adapter. Region: us-east-2.
- **Deploy branch:** `main`
- **CI/CD pipeline** (`.github/workflows/deploy.yml`): push to `main` → lint/test → security scan (pip-audit, safety) → Docker build/push to ECR → SAM deploy
- Manual fallback: `docker build && docker push && sam deploy` (see OPS_PLAYBOOK.md)
- **Infrastructure:** managed via SAM (`template.yaml`): Lambda + Function URL + CloudFront + ACM + Route 53
- Production requires RDS env vars (`RDS_DB_NAME`, `RDS_HOSTNAME`, etc.) set as Lambda environment variables
- S3 access: Lambda execution role grants S3 access to the `chemically-env` bucket
- **OPS_PLAYBOOK.md** at repo root has full infrastructure runbook: AWS resource inventory, secrets matrix, disaster recovery, rollback procedures, security hardening priorities

## Conventions

- Flake8: max line length 88, ignores E203/W503 (Black-compatible)
- Python 3.11/3.12 in CI
- Tests use `pytest-django` with Django `TestCase` classes (not pytest-style functions)
- Session stores substance history and calculator form state
- Templates live in `templates/chemistry_calculators/` and `templates/hplc_simulator/` (project-level `TEMPLATES.DIRS`)
- HPLC simulator has a `SCIENTIFIC_LOGIC.md` documenting physical invariants — any changes to the simulation engine must respect these constraints

## Agent Conduct

- **Branch guard**: Before committing to any pre-existing branch (`main`, `eb`, or any branch that already exists before the current session), ask the user to confirm the target branch. Use worktrees (`createworktree`) for new feature branches.
