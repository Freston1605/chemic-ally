# AGENTS.md — ChemicAlly

## Project

Django 5.2 web app providing chemistry calculators (molecular weight, reaction balancing, dilution, equilibria) and an HPLC chromatography simulator. Deployed on AWS Elastic Beanstalk (Python 3.14, us-east-2). Domain: chemic-ally.xyz.

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
pytest -v                      # preferred, covers webapp + hplc_simulator
python manage.py test webapp   # Django runner, webapp only (no HPLC tests)

# CI order: lint → deploy check → pytest → Django tests → security scan
```

## Settings

- `config/settings/base.py` — shared config. Loads `.env` via `python-dotenv`. Adds `rest_framework` and `hplc_simulator` to INSTALLED_APPS.
- `config/settings/development.py` — SQLite, DEBUG=True, local file storage.
- `config/settings/production.py` — PostgreSQL (RDS env vars), S3 storage, strict HTTPS. **Raises `ImproperlyConfigured`** if required env vars are missing. Uses `ALLOWED_HOSTS +=` (appends to base, does not replace).
- `manage.py` defaults to `config.settings.development`.
- `pytest.ini` also uses `config.settings.development`.
- EB sets `DJANGO_SETTINGS_MODULE=config.settings.production`.

## Architecture

```
apps/
  webapp/                     # Original Django app — chemistry calculators
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
  asgi.py
config/                       # Django project config
  settings/                   # base.py, development.py, production.py
  storage_backends.py         # StaticStorage (public) + MediaStorage (signed URLs)
  urls.py                     # Includes webapp (root) + hplc_simulator (/hplc/)
tests/
  webapp/                     # webapp tests
  hplc_simulator/             # hplc_simulator tests (engine, models, validation)
```

- `base.py` adds `apps/` and `chemically/` to `sys.path` so imports work as `webapp` and `hplc_simulator`.
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

## Deploy (AWS Elastic Beanstalk)

- Platform: Python 3.14 on Amazon Linux 2023, region: us-east-2
- Deploy branch: `eb` (see `.elasticbeanstalk/config.yml`)
- **CI/CD pipeline** (`.github/workflows/deploy.yml`): push to `eb` → lint/test (3.11+3.12) → security scan (pip-audit, safety) → build zip → deploy via AWS CLI → health check
- Manual fallback: `git checkout eb && git merge main && eb deploy`
- `.ebextensions/django.config` runs migrate + collectstatic (leader_only)
- `.ebextensions/https-instance.config` handles certbot/Let's Encrypt SSL (DNS-01 via Route 53, HTTP-01 fallback)
- Production requires RDS env vars (`RDS_DB_NAME`, `RDS_HOSTNAME`, etc.) — auto-injected by EB
- S3 access: EC2 instance profile has S3 access; hardcoded AWS keys in EB env props are redundant
- **OPS_PLAYBOOK.md** at repo root has full infrastructure runbook: AWS resource inventory, secrets matrix, disaster recovery, rollback procedures, security hardening priorities

## Conventions

- Flake8: max line length 88, ignores E203/W503 (Black-compatible)
- Python 3.11/3.12 in CI
- Tests use `pytest-django` with Django `TestCase` classes (not pytest-style functions)
- Session stores substance history and calculator form state
- Templates live in `templates/webapp/` and `templates/hplc_simulator/` (project-level `TEMPLATES.DIRS`)
- HPLC simulator has a `SCIENTIFIC_LOGIC.md` documenting physical invariants — any changes to the simulation engine must respect these constraints
