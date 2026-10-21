# OPS_PLAYBOOK.md — ChemicAlly Infrastructure Runbook

> **Purpose:** This document aligns engineers and operational agents on the deployment architecture, secrets management, disaster recovery, and day-to-day operational procedures for ChemicAlly.
>
> **Last updated:** 2026-10-21
> **AWS Account:** `768125641662`
> **Region:** `us-east-2` (application stack) / `us-east-1` (edge stack)

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
| **RDS Instance** | `chemically-db` | PostgreSQL 18.6, db.t3.micro, 20GB gp3, single-AZ, encrypted |
| **RDS Endpoint** | `chemically-db.cb46qw8cwu75.us-east-2.rds.amazonaws.com:5432` | Set as Lambda env vars via SSM `/chemically/rds/endpoint` |
| **Multi-AZ** | No | Single-AZ deployment |
| **DB User** | `chemically` | DB name `chemically_db` |

### Networking

| Resource | ID | Details |
|----------|----|---------|
| **VPC** | `vpc-0dbf831689e8a98b9` | — |
| **Lambda Security Group** | Managed by SAM | Outbound: all traffic, Inbound: VPC CIDR |
| **Custom Domain** | `chemic-ally.xyz` | DNS hosted in Cloudflare (free plan); SSL via ACM (auto-renewing, DNS-validated through Cloudflare) |

### Storage

| Resource | ID | Details |
|----------|----|---------|
| **App S3 Bucket** | `chemically-env-768125641662` | Static/media files via django-storages |

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
| `ORIGIN_SECRET` | Shared secret CloudFront stamps on every origin request (M006/S03, R029) | Deploy (SAM `OriginSecret` parameter) | Yes |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket for static files (`chemically-env`) | Build (collectstatic) | Yes |
| `ROUTE53_HOSTED_ZONE_ID` | (removed) Route 53 is no longer used | — | No |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token with `Zone:DNS:Edit` on chemic-ally.xyz | Deploy (ACM validation + DNS sync) | Yes |
| `CLOUDFLARE_ZONE_ID` | Cloudflare zone id for chemic-ally.xyz | Deploy (ACM validation + DNS sync) | Yes |

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
| `ORIGIN_HEADER_NAME` | `X-ChemicAlly-Origin` | Hardcoded in template (CloudFront origin custom header) |
| `ORIGIN_HEADER_SECRET` | SAM parameter `OriginSecret` (NoEcho) | Required — the origin header guard is inert without it |

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
# NOTE: the OriginSecret parameter has no default on purpose -- it carries the
# CloudFront origin header guard secret and must never be committed. Supply it
# from the secret store (or read it back from the current stack).
# NOTE (M007/S01): the app stack contains NO WAFv2 resource. A CloudFront-scoped
# web ACL can only be created in us-east-1, so the edge rate limit lives in the
# separate us-east-1 edge stack -- see the section below.
sam deploy \
  --template-file template.yaml \
  --stack-name chemically \
  --capabilities CAPABILITY_IAM \
  --region us-east-2 \
  --parameter-overrides \
    ImageTag=manual-$(date +%Y%m%d%H%M%S) \
    OriginSecret="$ORIGIN_SECRET"
```

### The two-stack deployment: app stack (us-east-2) and edge stack (us-east-1)

**Why two stacks.** AWS accepts WAFv2 web ACLs with `Scope: CLOUDFRONT` only
in us-east-1, while the application stack (Lambda, VPC, RDS) lives in
us-east-2 (D015/D018). The edge rate limit therefore lives in its own stack,
`chemically-edge`, and takes the live distribution's ARN as a parameter. The
app stack (`chemically`) keeps the distribution and exports its ARN.

| Stack | Template | Region | Contents |
|-------|----------|--------|----------|
| `chemically` | `template.yaml` | us-east-2 | Lambda, VPC, RDS, S3, the CloudFront distribution (exports `DistributionArn`) |
| `chemically-edge` | `edge-template.yaml` | us-east-1 | the WAF web ACL and its `WebACLAssociation` (takes `DistributionArn`) |

Since M007/S01 the app stack declares **no** `AWS::WAFv2` resource at all:
declaring one in us-east-2 makes every deploy fail on a resource that region
cannot create. The edge stack is the only home of the rate limit.

**Order (never reversed):**
1. Deploy the app stack (us-east-2). It owns the distribution and exports
   `DistributionArn`.
2. Deploy the edge stack (us-east-1) with that ARN.
3. Verify the association exists against the live distribution -- a detached
   control must never pass silently.

**Keep the distribution's identity on record.** The edge stack is handed the
distribution's ARN once. CloudFormation updates a CloudFront distribution IN
PLACE while its logical ID is unchanged, so the ID -- and the ARN -- survive
an ordinary deploy; they change only if the stack is deleted and recreated.
Capture the identity before a deploy and compare after it, so a replaced
distribution is caught instead of silently detaching the edge association:

```bash
# BEFORE the app-stack deploy: capture the live identity
python scripts/verify_distribution_identity.py --region us-east-2 \
  | tee /tmp/distribution-before.txt
BEFORE_ID=$(grep '^DISTRIBUTION_ID=' /tmp/distribution-before.txt | cut -d= -f2)

# AFTER the app-stack deploy: the ID must be unchanged
python scripts/verify_distribution_identity.py --region us-east-2 \
  --expect-distribution-id "$BEFORE_ID"
```

The script is read-only (`cloudformation:DescribeStacks` and
`cloudfront:ListDistributions`) and exits non-zero when the stack is not in
a state that describes the live deployment, when the exported
`DistributionArn` does not match the live distribution, or when the
distribution ID changed.

```bash
# 1. App stack (us-east-2)
sam deploy \
  --template-file template.yaml \
  --stack-name chemically \
  --capabilities CAPABILITY_IAM \
  --region us-east-2 \
  --parameter-overrides \
    ImageTag="$IMAGE_TAG" \
    CertificateArn="$CERTIFICATE_ARN" \
    RDSPassword="$RDS_PASSWORD" \
    DjangoSecretKey="$DJANGO_SECRET_KEY" \
    OriginSecret="$ORIGIN_SECRET"

# 2. Read the live distribution ARN out of the app stack's outputs
DIST_ARN=$(aws cloudformation describe-stacks \
  --stack-name chemically --region us-east-2 \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionArn'].OutputValue" \
  --output text)

# 3. Edge stack (us-east-1) -- Count mode (the canary)
sam deploy \
  --template-file edge-template.yaml \
  --stack-name chemically-edge \
  --region us-east-1 \
  --parameter-overrides \
    DistributionArn="$DIST_ARN" \
    WafRateLimit=2000 \
    WafAction=Count \
  --no-fail-on-empty-changeset

# 4. Verify the association exists
EDGE_ACL=$(aws cloudformation describe-stacks \
  --stack-name chemically-edge --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='WebACLArn'].OutputValue" \
  --output text)
aws wafv2 list-resources-for-web-acl --web-acl-arn "$EDGE_ACL" --region us-east-1
```

**Switching the rule from Count to Block (the canary, and the rollback):**

```bash
sam deploy --template-file edge-template.yaml --stack-name chemically-edge \
  --region us-east-1 \
  --parameter-overrides DistributionArn="$DIST_ARN" WafRateLimit=2000 WafAction=Block
```

`Count` measures the traffic without touching a single request; `Block`
refuses it at the edge, before Lambda. The same parameter change is the
rollback, so the canary never needs a code change.

**CI owns the edge stack's action.** The deploy job sets the action from
ONE workflow setting, `WAF_ACTION` (default `Count`), in
`.github/workflows/deploy.yml`, and it re-deploys the edge stack after every
app deploy (the app stack's `DistributionArn` is re-read on each run). A
manual `sam deploy ... WafAction=Block` is therefore **reverted by the next
CI run**, which redeploys the edge stack with `WAF_ACTION=Count`, unless
that workflow setting is changed to `Block` at the same time. To switch the
canary for real: change `WAF_ACTION` in the workflow and push to `main`.
A manual deploy alone does not survive the next CI run, so a rollback that
must hold has to be made in the workflow.

**Credentials:** the deploy principal needs WAFv2 and CloudFormation
permissions in us-east-1 in addition to us-east-2. The GitHub Actions
credentials (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) are account-wide,
so the region is a parameter, not a second credential set.

**Rollback:** the two stacks are independent. If the edge deploy fails, the
site keeps serving and the in-app throttles (D016) remain the only bound --
re-run the edge deploy. The app stack never depends on the edge stack.

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

3. **Origin secret (M006/S03, R029):**
   - Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - Update the GitHub secret `ORIGIN_SECRET`
   - Redeploy: the new value must reach BOTH the Lambda environment
     (`ORIGIN_HEADER_SECRET`) and the CloudFront origin custom header. They are
     set from the same SAM parameter, so a single redeploy updates both
     atomically. Until the redeploy lands, the old value is still the live
     one -- do not rotate it out of band.

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
| **DNS (Cloudflare)** | Free | Authoritative DNS on the Cloudflare free plan; replaces Route 53 ($0.50/mo) |
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

The Function URL should only accept traffic from CloudFront. The Lambda resource policy in `template.yaml` grants `lambda:InvokeFunctionUrl` to `Principal "*"` (CloudFront cannot sign requests), so the Function URL is reachable by anyone who learns its URL. The bypass around the WAF is therefore closed in the application instead: CloudFront stamps every origin request with the `X-ChemicAlly-Origin` header (value: the NoEcho `OriginSecret` SAM parameter) and the Django middleware `chemically.middleware.OriginHeaderGuardMiddleware` refuses any request that does not carry it with 403.

Verify the guard is live:

```bash
# Must fail with 403: a direct Function-URL request carries no origin header.
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://<function-url-id>.lambda-url.us-east-2.on.aws/"

# Must succeed with 200: the public site goes through CloudFront.
curl -s -o /dev/null -w '%{http_code}\n' https://chemic-ally.xyz/

aws lambda get-resource-policy \
  --function-name chemically-DjangoFunction-XXXXX \
  --region us-east-2
```

Rollout note: the first deploy that enables the guard updates the Lambda environment and the CloudFront distribution in the same stack operation, and CloudFormation does not guarantee their order. If the function is updated first, requests are refused with 403 until the distribution starts stamping the header (a one-time window of seconds). The `live-probe` job after the deploy is the gate that catches it.

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
- [ ] Point Cloudflare DNS (apex + www) at CloudFront; ACM cert DNS-validated via Cloudflare
- [ ] Disable old EB environment (do not delete immediately)
- [ ] Monitor for errors in CloudWatch
- [ ] Delete EB environment + application once stable
- [ ] Delete `.ebextensions/`, `.platform/`, `.elasticbeanstalk/` from repo
- [ ] Update `OPS_PLAYBOOK.md`

---

*Last updated: 2026-06-08 | Maintained by: DevOps / Engineering Team*
