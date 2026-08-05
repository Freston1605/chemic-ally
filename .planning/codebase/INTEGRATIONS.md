# External Integrations

**Analysis Date:** 2026-08-05

## APIs & External Services

**Outbound server-side APIs:** None. The application makes no outbound HTTP calls to third-party APIs — a grep for `requests`, `httpx`, `aiohttp`, `urllib.request`, `stripe`, `sendgrid`, `twilio`, `openai`, `anthropic`, and direct `boto3.client/resource` usage across `apps/`, `config/`, and `chemically/` returns no matches. All computation (chemistry + HPLC simulation) is local.

**Inbound CDN assets (browser-side, not server integrations):**
- Bootstrap 5.3.2 CSS/JS + Popper 2.11.8 — jsDelivr, in `templates/chemistry_calculators/base.html` (SRI hashes pinned)
- MathJax 3 — jsDelivr `tex-mml-chtml.js`, client-side LaTeX rendering in `templates/chemistry_calculators/base.html`
- Plotly 2.27.0 — `cdn.plot.ly`, chromatogram rendering in `templates/hplc_simulator/hplc_base.html`
- Alpine.js 3.x — jsDelivr, in `templates/hplc_simulator/hplc_base.html`
- polyfill.io — cdnjs, `IntersectionObserver`/`Promise` polyfill in `templates/chemistry_calculators/base.html`
- Google Fonts (Inter) — `fonts.googleapis.com` in `templates/chemistry_calculators/base.html`

## Data Storage

**Databases:**
- PostgreSQL via AWS RDS (production) — `config/settings/production.py` uses `django.db.backends.postgresql` with env vars `RDS_DB_NAME`, `RDS_USERNAME`, `RDS_PASSWORD`, `RDS_HOSTNAME`, `RDS_PORT`; `CONN_MAX_AGE=0` (Lambda-compatible). Lambda runs in a VPC with a security group allowing RDS access (`template.yaml` LambdaSecurityGroup, subnets/VPC from SSM `/chemically/lambda/subnet_ids` and `/chemically/lambda/vpc_id`). Client: psycopg2-binary (Django ORM).
- SQLite (development) — `config/settings/development.py`, file `db.sqlite3`.
- Tables: `Analyte`, `Level`, `UserScore`, `LevelProgress` (`apps/hplc_simulator/models.py`, migrations `0001`–`0003`). Django framework tables via `python manage.py migrate`.

**File Storage:**
- Amazon S3 via django-storages (`config/storage_backends.py`):
  - `StaticStorage` — `static/` prefix, public-read, `CacheControl: max-age=31536000`, no querystring auth. Used by `collectstatic` (run in CI with AWS creds).
  - `MediaStorage` — `media/` prefix, private, signed URLs with 1-hour expiry (`querystring_expire = 3600`).
  - Bucket name from `AWS_STORAGE_BUCKET_NAME` env (SSM param `/chemically/environment/bucket_name` → `chemically-env` bucket). Access via Lambda execution role IAM policy (s3:GetObject/PutObject/DeleteObject/ListBucket), not static keys (`template.yaml`).
- Local filesystem (development): `FileSystemStorage` / `StaticFilesStorage` (`config/settings/development.py`).

**Caching:**
- None. No Redis/Memcached; no `CACHES` setting. Session data uses Django's default database-backed session store (`django.contrib.sessions`).

## Authentication & Identity

**Auth Provider:**
- Custom, no external provider. Django's built-in `django.contrib.auth` session authentication; no OAuth/social auth apps installed.
- HPLC simulator API endpoints use DRF `AllowAny` permission (`apps/hplc_simulator/views.py`) — no API keys or tokens; per-user state is tracked by `session_key` on `UserScore`/`LevelProgress` models.
- Lambda Function URL is `AuthType: NONE` (`template.yaml`), gated at the edge by CloudFront only.

## Monitoring & Observability

**Error Tracking:**
- None. No Sentry, Rollbar, or equivalent.
- Security scanning in CI only: pip-audit + safety (`pip-audit --format json`, `safety check --output json`) with artifacts uploaded, in `.github/workflows/deploy.yml`.

**Logs:**
- Python stdlib `logging` with a console `StreamHandler` and verbose formatter (`config/settings/base.py`); production overrides `django.request` and `django.security` loggers to WARNING. On Lambda these go to stdout/stderr → Amazon CloudWatch Logs. No structured logging library.

## CI/CD & Deployment

**Hosting:**
- AWS Lambda (container image, 1024 MB, 30s timeout, region us-east-2) — Mangum ASGI handler (`config/asgi.py`), ECR repo `chemically`.
- Edge: CloudFront distribution (PriceClass_100, HTTP→HTTPS redirect, all cookies/headers forwarded, TTL 0) with ACM cert (us-east-1, DNS validation via Route 53) and Route 53 A records for `chemic-ally.xyz` and `www.chemic-ally.xyz` (`template.yaml`).
- Infrastructure as code: AWS SAM (`template.yaml`), stack name `chemically`.

**CI Pipeline:**
- GitHub Actions (`.github/workflows/deploy.yml`), triggered on push/PR to `main`. Order: lint (flake8) → test (check --deploy + pytest) → security scan (pip-audit, safety) → build (Docker → ECR, `linux/amd64`, no-cache) → SAM deploy (only on push to main) → Lambda verification. Deploy branch: `main`.
- AWS account 768125641662; GitHub Secrets: `DJANGO_SECRET_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `RDS_PASSWORD`, `ROUTE53_HOSTED_ZONE_ID`.

## Environment Configuration

**Required env vars (production, set as Lambda environment variables):**
- `SECRET_KEY` (Django signing key)
- `RDS_DB_NAME`, `RDS_USERNAME`, `RDS_PASSWORD`, `RDS_HOSTNAME`, `RDS_PORT` (RDS PostgreSQL connection)
- `AWS_STORAGE_BUCKET_NAME` (S3 bucket for static/media)
- `ALLOWED_HOSTS` (defaults to `.chemic-ally.xyz` in `config/settings/production.py`)

**Optional env vars:**
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (only needed for local `collectstatic` testing; Lambda role handles S3 in production)
- `AWS_S3_REGION_NAME` (default `us-east-2`), `AWS_S3_CUSTOM_DOMAIN`

**Secrets location:**
- Production: Lambda environment variables injected by SAM from parameters — secrets passed as `NoEcho` template parameters (`RDSPassword`, `DjangoSecretKey`); non-secret config pulled from SSM Parameter Store (`/chemically/rds/db_name`, `/chemically/rds/username`, `/chemically/rds/endpoint`, `/chemically/lambda/subnet_ids`, `/chemically/lambda/vpc_id`, `/chemically/environment/bucket_name`).
- CI: GitHub Actions secrets (listed above).
- Local: `.env` file (exists at repo root; contents must never be read/committed — `.env.example` documents the schema).

## Webhooks & Callbacks

**Incoming:**
- None.

**Outgoing:**
- None. No webhook delivery, event publishing, or external callback registration anywhere in the codebase.

---

*Integration audit: 2026-08-05*
