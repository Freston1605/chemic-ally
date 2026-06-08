# OPS_PLAYBOOK.md — ChemicAlly Infrastructure Runbook

> **Purpose:** This document aligns engineers and operational agents on the deployment architecture, secrets management, disaster recovery, and day-to-day operational procedures for ChemicAlly.
>
> **Last updated:** 2026-06-08
> **AWS Account:** `768125641662`
> **Region:** `us-east-2`

---

## 1. Environment Architecture

### Pipeline Flow

```
┌──────────────┐     ┌──────────────────────────────────┐     ┌───────────────────┐     ┌──────────────────────────┐
│  Developer   │────▶│  GitHub Actions Runner             │────▶│  ECR + Lambda      │────▶│  Production Environment  │
│  Push to     │     │  (ubuntu-latest)                   │     │  (us-east-2)       │     │                          │
│  main        │     │                                    │     │                    │     │  Lambda (1024MB, 30s)    │
│              │     │  1. Lint & Test (3.14)             │     │  1. Docker build   │     │  └─ Function URL         │
│              │     │  2. Security Scan (pip-audit)      │     │  2. Push to ECR    │     │  CloudFront (CDN + SSL)  │
│              │     │  3. Build Docker image             │     │  3. SAM deploy     │     │  RDS: PostgreSQL 18.3    │
│              │     │  4. Deploy via SAM                 │     │                    │     │  S3: chemically-env      │
│              │     │                                    │     │                    │     │  Domain: chemic-ally.xyz │
└──────────────┘     └──────────────────────────────────┘     └───────────────────┘     └──────────────────────────┘
```

### Environment Matrix

| Environment | Branch | Settings Module | Database | Deploy Trigger |
|-------------|--------|-----------------|----------|----------------|
| **Local Dev** | Any feature branch | `config.settings.development` | SQLite (`db.sqlite3`) | N/A — `python manage.py runserver` |
| **CI/Runner** | PR to `main` | `config.settings.development` | SQLite (ephemeral) | Push/PR to `main` |
| **Production** | `main` | `config.settings.production` | RDS PostgreSQL 18.3 (5GB, single-AZ) | Push to `main` via CI |

---

## 2. Live AWS Resource Inventory

### Compute

| Resource | ID | Details |
|----------|----|---------|
| **Lambda Function** | `chemically-DjangoFunction-*` | Container image, 1024MB, 30s timeout, Python 3.14 |
| **Function URL** | `*.lambda-url.us-east-2.on.aws` | AuthType: NONE (restricted to CloudFront) |
| **ECR Repository** | `chemically` | Stores container images, scan on push, 10-image retention |
| **CloudFront Distribution** | `*` | Custom domain: chemic-ally.xyz, origin: Function URL |

### Database

| Resource | ID | Details |
|----------|----|---------|
| **RDS Instance** | `awseb-e-2fcyjhgyu9-stack-awsebrdsdatabase-7dafhgsyrlmy` | PostgreSQL 18.3, db.t3.micro, 5GB |
| **RDS Endpoint** | `*.cb46qw8cwu75.us-east-2.rds.amazonaws.com:5432` | Set as Lambda env vars |
| **Multi-AZ** | No | Single-AZ deployment |
| **DB User** | `ebroot` | — |

### Networking

| Resource | ID | Details |
|----------|----|---------|
| **VPC** | `vpc-0dbf831689e8a98b9` | — |
| **Lambda Security Group** | Managed by SAM | Outbound: all traffic, Inbound: VPC CIDR |
| **Custom Domain** | `chemic-ally.xyz` | SSL via ACM (auto-renewing) |

### Storage

| Resource | ID | Details |
|----------|----|---------|
| **App S3 Bucket** | `chemically-env` | Static/media files via django-storages |

### IAM

| Resource | ARN | Purpose |
|----------|-----|---------|
| **Lambda Execution Role** | Managed by SAM | Grants logs (CloudWatch), S3 access, VPC access |
| **Deploy IAM User** | `arn:aws:iam::768125641662:user/Franco` | Used by CI/CD |

---

## 3. Environment Variables & Secrets Matrix

### GitHub Actions Secrets

Configure in **GitHub → Repository → Settings → Secrets and variables → Actions**:

| Secret Name | Description | Used In | Required |
|-------------|-------------|---------|----------|
| `AWS_ACCESS_KEY_ID` | IAM user access key | Build + Deploy | Yes |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key | Build + Deploy | Yes |
| `DJANGO_SECRET_KEY` | Django signing key | Build (collectstatic) | Yes |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket for static files (`chemically-env`) | Build (collectstatic) | Yes |
| `ROUTE53_HOSTED_ZONE_ID` | Route 53 hosted zone ID | SAM deploy | Yes |

### Lambda Environment Variables

Set in the SAM `template.yaml` under `Function.Environment.Variables` or via the Lambda console.

| Variable | Source | Notes |
|----------|--------|-------|
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | Hardcoded in template |
| `ALLOWED_HOSTS` | `.chemic-ally.xyz` | Hardcoded in template |
| `SECRET_KEY` | SSM Parameter Store or env var | Required |
| `AWS_STORAGE_BUCKET_NAME` | `chemically-env` | Required |
| `AWS_S3_REGION_NAME` | `us-east-2` | Required |
| `RDS_DB_NAME` | RDS instance value | Required |
| `RDS_USERNAME` | `ebroot` | Required |
| `RDS_PASSWORD` | RDS instance value | Required |
| `RDS_HOSTNAME` | RDS endpoint | Required |
| `RDS_PORT` | `5432` | Required |

### RDS Variables

Same connection details as before, now set explicitly as Lambda environment variables (EB no longer auto-injects them).

### OIDC Setup (Recommended Future Improvement)

Follow the same OIDC setup documented in the previous version of this playbook. Replace EB deploy permissions with:
- ECR: `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`
- Lambda: `lambda:UpdateFunctionCode`, `lambda:GetFunction`
- CloudFormation: `cloudformation:DescribeStacks`, `cloudformation:CreateChangeSet`, `cloudformation:ExecuteChangeSet`

---

## 4. Disaster Recovery & Rollback Protocols

### Immediate Rollback (< 2 minutes)

#### Option A: Re-deploy previous image tag

```bash
# Find the previous image tag in ECR
aws ecr describe-images --repository-name chemically --region us-east-2

# Deploy the previous image
aws lambda update-function-code \
  --function-name chemically-DjangoFunction-XXXXX \
  --image-uri 768125641662.dkr.ecr.us-east-2.amazonaws.com/chemically:<PREVIOUS_TAG> \
  --region us-east-2
```

#### Option B: SAM rollback

```bash
# List stack events to find previous version
aws cloudformation describe-stack-events \
  --stack-name chemically \
  --region us-east-2

# Rollback via CloudFormation
aws cloudformation rollback-stack \
  --stack-name chemically \
  --region us-east-2
```

#### Option C: Git Revert

```bash
git checkout main
git revert HEAD
git push origin main
# CI pipeline builds and deploys the reverted code
```

### Database Migration Rollback

```bash
# Invoke a one-off Lambda with a management command
# Temporarily set the handler to run migrations, or run locally:
# (requires network access to RDS)
python manage.py migrate chemistry_calculators <previous_migration_number>

# Or via Lambda: create a custom handler that runs migrations
```

> **WARNING:** Reversing migrations that drop columns or delete data is **not reversible**. Always backup RDS before running migrations.

### RDS Backup & Restore

```bash
# Create manual snapshot before risky operations
aws rds create-db-snapshot \
  --db-instance-identifier awseb-e-2fcyjhgyu9-stack-awsebrdsdatabase-7dafhgsyrlmy \
  --db-snapshot-identifier pre-deploy-$(date +%Y%m%d-%H%M%S) \
  --region us-east-2

# Restore from snapshot (creates new instance)
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier chemically-restored \
  --db-snapshot-identifier <SNAPSHOT_ID> \
  --region us-east-2
```

### Incident Response Checklist

| Step | Action | Owner |
|------|--------|-------|
| 1 | Identify breaking change (monitoring alert, user report) | On-call |
| 2 | Execute rollback (Option A/B/C above) | On-call |
| 3 | Verify rollback success — smoke test the site | On-call |
| 4 | Create incident ticket with root cause analysis | Engineering |
| 5 | Fix issue in feature branch, re-run CI/CD pipeline | Engineering |
| 6 | Post-incident review and update runbook | Team |

---

## 5. Operational Procedures

### Deploying a New Version

**Automated (CI/CD):**
1. Merge code to `main` branch
2. GitHub Actions pipeline: Lint → Test → Security Scan → Docker build/push → SAM deploy
3. Monitor: AWS Lambda console or CloudWatch

**Manual (fallback):**
```bash
# Build and push Docker image
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 768125641662.dkr.ecr.us-east-2.amazonaws.com
docker build -t chemically .
docker tag chemically:latest 768125641662.dkr.ecr.us-east-2.amazonaws.com/chemically:manual-$(date +%Y%m%d%H%M%S)
docker push 768125641662.dkr.ecr.us-east-2.amazonaws.com/chemically:manual-$(date +%Y%m%d%H%M%S)

# Deploy
sam deploy \
  --template-file template.yaml \
  --stack-name chemically \
  --capabilities CAPABILITY_IAM \
  --region us-east-2 \
  --parameter-overrides ImageTag=manual-$(date +%Y%m%d%H%M%S)
```

### Running Django Management Commands

Lambda does not have an interactive shell. Use one of:

```bash
# Option 1: Invoke Lambda with a custom event (requires a handler that accepts commands)
aws lambda invoke \
  --function-name chemically-DjangoFunction-XXXXX \
  --payload '{"command": "migrate"}' \
  --region us-east-2 \
  response.json

# Option 2: Run locally with production settings (requires RDS network access)
DJANGO_SETTINGS_MODULE=config.settings.production python manage.py <command>
```

### Viewing Application Logs

```bash
# CloudWatch Logs (primary)
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/chemically

# Tail recent Lambda logs
aws logs tail /aws/lambda/chemically-DjangoFunction-XXXXX --follow --region us-east-2

# CloudWatch Logs Insight query (SQL-like)
# Fields: @timestamp, @requestId, @message, @logStream
# Filter: { $.levelname = "ERROR" }
```

### Rotating Secrets

1. **Django SECRET_KEY:**
   - Generate: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
   - Update Lambda environment variable
   - Update GitHub secret `DJANGO_SECRET_KEY`
   - Redeploy

2. **AWS Credentials (CI):**
   - Rotate IAM user keys in AWS IAM Console
   - Update GitHub secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   - No redeploy needed

### Local Development with Lambda Emulation

```bash
# Option 1: Standard Django dev server
python manage.py runserver

# Option 2: Test the container locally
docker build -t chemically .
docker run -p 9000:8080 chemically:latest
# Then visit http://localhost:9000/2015-03-31/functions/function/invocations
# Send a test event: {"httpMethod": "GET", "path": "/", "headers": {}}
```

---

## 6. Cost Analysis

| Item | Monthly Cost | Notes |
|------|-------------|-------|
| **Lambda** | ~$0.01-0.10 | Pay-per-request, ~1M requests/mo estimate |
| **ECR** | ~$0.01 | Storage for container images |
| **CloudFront** | ~$0 | Free tier covers low traffic |
| **ACM** | Free | Auto-renewing SSL certificates |
| **RDS PostgreSQL** | ~$15 | Unchanged from EB architecture |
| **S3** | ~$0.10 | Static/media file storage |
| **Total (exc. RDS)** | **~$0.12-0.21/mo** | |
| **Total (inc. RDS)** | **~$15.12-15.21/mo** | |

**Previous EB cost:** ~$23.50/mo (EC2 t3.micro $8.50 + RDS $15)
**Lambda savings:** ~$8.30/mo (EC2 cost eliminated)

---

## 7. Security Hardening

### Priority 1 — Lambda Execution Role

The Lambda execution role should have least-privilege permissions:
- S3: only the `chemically-env` bucket
- RDS: only via VPC (no public access)
- CloudWatch: only `CreateLogGroup`, `CreateLogStream`, `PutLogEvents`

### Priority 2 — Restrict Function URL

The Function URL should only accept traffic from CloudFront. The resource policy is already configured in `template.yaml` to restrict by CloudFront distribution ARN. Verify:

```bash
aws lambda get-resource-policy \
  --function-name chemically-DjangoFunction-XXXXX \
  --region us-east-2
```

### Priority 3 — Enable CloudWatch Alarms

| Metric | Namespace | Threshold | Action |
|--------|-----------|-----------|--------|
| `Errors` | AWS/Lambda | > 0 over 5 min | SNS alert |
| `Throttles` | AWS/Lambda | > 0 over 5 min | SNS alert |
| `Duration` | AWS/Lambda | > 25000 ms | SNS alert |
| `DatabaseConnections` | AWS/RDS | > 80% of max | SNS alert |

### Priority 4 — Enable Lambda Reserved Concurrency

Set reserved concurrency to prevent runaway costs:

```bash
aws lambda put-function-concurrency \
  --function-name chemically-DjangoFunction-XXXXX \
  --reserved-concurrent-executions 5 \
  --region us-east-2
```

This limits concurrent executions to 5, capping worst-case monthly cost.

---

## 8. Migration Checklist (EB → Lambda)

- [ ] Create `config/asgi.py` with Mangum
- [ ] Add Mangum to production dependencies
- [ ] Create `Dockerfile` for Lambda container image
- [ ] Update `config/settings/production.py` for Lambda
- [ ] Create `template.yaml` (SAM)
- [ ] Update CI/CD pipeline in `.github/workflows/deploy.yml`
- [ ] Verify CloudFront SSL certificate issues
- [ ] Test Lambda function with Function URL
- [ ] Update Route 53 DNS to point to CloudFront
- [ ] Disable old EB environment (do not delete immediately)
- [ ] Monitor for errors in CloudWatch
- [ ] Delete EB environment + application once stable
- [ ] Delete `.ebextensions/`, `.platform/`, `.elasticbeanstalk/` from repo
- [ ] Update `OPS_PLAYBOOK.md`

---

*Last updated: 2026-06-08 | Maintained by: DevOps / Engineering Team*
