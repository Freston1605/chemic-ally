# Technology Stack

**Analysis Date:** 2026-08-05

## Languages

**Primary:**
- Python 3.14 — Django application, chemistry calculation engines, HPLC simulation engine. Runtime is the AWS Lambda Python 3.14 container image (`Dockerfile` uses `public.ecr.aws/lambda/python:3.14`); CI pins `PYTHON_VERSION: "3.14"` in `.github/workflows/deploy.yml`.

**Secondary:**
- JavaScript (vanilla ES) — Browser-side form handling, chart rendering, dark mode. No framework bundler; plain files served from `static/js/` (`calculators.js`, `equilibria.js`, `substance_input.js`, `form_validation.js`, `clipboard.js`, `dark_mode_enabler.js`).
- HTML — Django template language, project-level templates in `templates/chemistry_calculators/` and `templates/hplc_simulator/`.
- CSS — Single hand-written stylesheet `static/css/style.css` plus Bootstrap 5.3.2 from CDN.

## Runtime

**Environment:**
- AWS Lambda (container image, `PackageType: Image`) — served via Mangum ASGI adapter. `config/asgi.py` exposes `handler = Mangum(application, lifespan="off")`; `Dockerfile` CMD is `config.asgi.handler`.
- Local development: Django dev server via `manage.py` (defaults to `config.settings.development`).
- Legacy WSGI path exists at `config/wsgi.py` (defaults to development settings) and `gunicorn==22.0.0` is listed in `requirements/production.txt` — a leftover from the Elastic Beanstalk era; the Lambda image installs from `requirements/requirements-lambda.txt`, which does **not** include gunicorn.

**Package Manager:**
- pip with layered requirements files (no lockfile, no pip-tools):
  - `requirements/base.txt` — runtime deps
  - `requirements/development.txt` — `-r base.txt` + pytest, pytest-django, flake8
  - `requirements/production.txt` — `-r base.txt` + gunicorn (legacy, EB-era)
  - `requirements/requirements-lambda.txt` — `-r base.txt` + psycopg2-binary, mangum (used by `Dockerfile` and CI security scan)

## Frameworks

**Core:**
- Django 5.2.3 — Web framework; server-rendered webapp plus `rest_framework` in `INSTALLED_APPS` (`config/settings/base.py`). Projects apps: `chemistry_calculators` (root URL) and `hplc_simulator` (`/hplc/`).
- Django REST Framework (djangorestframework 3.15.2) — HPLC simulator JSON API (`/hplc/api/*`). Config: `PageNumberPagination` with `PAGE_SIZE: 20`, JSON + Browsable API renderers (`config/settings/base.py`).

**Testing:**
- pytest 8.4.0 + pytest-django 4.11.1 — `pytest.ini` sets `DJANGO_SETTINGS_MODULE = config.settings.development`; tests under `tests/chemistry_calculators/` and `tests/hplc_simulator/`. CI runs `python manage.py check --deploy` and `pytest -v`.

**Build/Dev:**
- Docker (single-stage, `Dockerfile`) — build/push to ECR in CI, deploy via AWS SAM (`template.yaml`).
- flake8 7.0.0 — linting; max line length 88, ignores E203/W503 (Black-compatible).

## Key Dependencies

**Critical:**
- ChemPy 0.9.0 — chemical formula parsing (`Substance.from_formula`), reaction balancing (`balance_stoichiometry`), equilibria solving (`chempy.equilibria.EqSystem`) — `apps/chemistry_calculators/calculations/base.py`, `apps/chemistry_calculators/calculations/equilibria.py`.
- Pint 0.24.4 — unit-safe arithmetic; `ureg = pint.UnitRegistry()` and `Q_ = ureg.Quantity` in `apps/chemistry_calculators/calculations/units.py`.
- numpy>=2.1.0,<3 — chromatogram time arrays, signal generation, EMG peak math — `apps/hplc_simulator/simulation/engine.py`.
- scipy>=1.14.0,<2 — `scipy.special.erf` for exponentially modified Gaussian (EMG) peaks — `apps/hplc_simulator/simulation/engine.py`.
- pyparsing 3.2.3 — transitive dependency of ChemPy (formula parsing), pinned explicitly in `requirements/base.txt`.

**Infrastructure:**
- boto3 1.38.7 — AWS SDK, used by django-storages S3 backend (no direct boto3 client calls in app code).
- django-storages 1.14.5 — S3 storage backends: `StaticStorage` (public, 1-year cache) and `MediaStorage` (signed URLs, 1-hour expiry) in `config/storage_backends.py`.
- psycopg2-binary — PostgreSQL driver (production only, RDS).
- mangum>=0.19 — ASGI-to-Lambda adapter (`config/asgi.py`).
- python-dotenv 1.1.0 — loads `.env` in `config/settings/base.py` via `load_dotenv()`.

## Configuration

**Environment:**
- Settings split across `config/settings/base.py`, `config/settings/development.py`, `config/settings/production.py`.
- `.env` file present at repo root (local dev; loaded by python-dotenv) with `.env.example` documenting required vars. **Do not read `.env` contents — secrets live there.**
- `production.py` raises `ImproperlyConfigured` for missing `SECRET_KEY`, `RDS_*`, `AWS_STORAGE_BUCKET_NAME`; `ALLOWED_HOSTS` defaults to `.chemic-ally.xyz`.
- Lambda runtime sets `DJANGO_SETTINGS_MODULE=config.settings.production` (in `template.yaml` Globals and `config/asgi.py`).

**Build:**
- `Dockerfile` — Lambda Python 3.14 base, installs `requirements/requirements-lambda.txt`, copies repo, CMD `config.asgi.handler`.
- `template.yaml` — SAM/CloudFormation: Lambda function (1024 MB, 30s timeout), Function URL (`AuthType: NONE`), CloudFront distribution, ACM cert (us-east-1), Route 53 A records, Lambda execution role with S3 access, VPC config for RDS.
- `pytest.ini` — test settings and file patterns (`test_*.py`, `*_test.py`).

## Platform Requirements

**Development:**
- Python 3.11+ (project conventions; CI/Lambda use 3.14), virtualenv recommended.
- SQLite via `config/settings/development.py` — no external services required.
- Seed command after migrate: `python manage.py seed_hplc_data` (populates Analyte/Level data for the HPLC simulator).

**Production:**
- AWS Lambda (container image, us-east-2), PostgreSQL on RDS (VPC-attached), S3 bucket for static/media, CloudFront + ACM + Route 53 for `chemic-ally.xyz`.
- RDS env vars (`RDS_DB_NAME`, `RDS_USERNAME`, `RDS_PASSWORD`, `RDS_HOSTNAME`, `RDS_PORT`) injected as Lambda environment variables via SAM (`template.yaml`), sourced from SSM Parameter Store paths `/chemically/rds/*`.

---

*Stack analysis: 2026-08-05*
