# OPS_PLAYBOOK.md — ChemicAlly Infrastructure Runbook

> **Purpose:** This document aligns engineers and operational agents on the deployment architecture, secrets management, disaster recovery, and day-to-day operational procedures for ChemicAlly.
>
> **Last updated:** 2026-05-17
> **AWS Account:** `768125641662`
> **Region:** `us-east-2`

---

## 1. Environment Architecture

### Pipeline Flow

```
┌──────────────┐     ┌──────────────────────────────────┐     ┌─────────────────────────────────────┐     ┌──────────────────────────────────────┐
│  Developer   │────▶│  GitHub Actions Runner             │────▶│  AWS Elastic Beanstalk API           │────▶│  Production Environment              │
│  Push to     │     │  (ubuntu-latest)                   │     │  (us-east-2)                         │     │                                      │
│  main / eb   │     │                                    │     │                                     │     │  EC2: i-02b5039d6b1e817a1 (t3.micro)  │
│              │     │  1. Lint & Test (3.11 + 3.12)      │     │  1. Create app version              │     │  Nginx → Gunicorn (1 proc/15 threads) │
│              │     │  2. Security Scan (pip-audit)      │     │  2. Upload ZIP to S3                │     │  RDS: PostgreSQL 18.3 (db.t3.micro)   │
│              │     │  3. Build ZIP artifact             │     │  3. update-environment (AllAtOnce)  │     │  S3: chemically-env (static/)         │
│              │     │  4. Deploy via AWS CLI             │     │  4. eb-activity runs migrations     │     │  Domain: chemic-ally.xyz              │
└──────────────┘     └──────────────────────────────────┘     └─────────────────────────────────────┘     │  Let's Encrypt SSL                  │
                                                                                                          │  SSH: 0.0.0.0/0 (⚠ restrict)          │
                                                                                                          └──────────────────────────────────────┘
```

### Environment Matrix

| Environment | Branch | Settings Module | Database | Deploy Trigger |
|-------------|--------|-----------------|----------|----------------|
| **Local Dev** | Any feature branch | `config.settings.development` | SQLite (`db.sqlite3`) | N/A — `python manage.py runserver` |
| **CI/Runner** | PR to `main` | `config.settings.development` | SQLite (ephemeral) | Push/PR to `main` |
| **Production** | `eb` | `config.settings.production` | RDS PostgreSQL 18.3 (5GB, single-AZ) | Push to `eb` branch via CI |

---

## 2. Live AWS Resource Inventory

### Compute

| Resource | ID | Details |
|----------|----|---------|
| **EB Application** | `chemically` | Platform: Python 3.14 on Amazon Linux 2023 v4.12.3 |
| **EB Environment** | `Chemically-env` (`e-2fcyjhgyu9`) | SingleInstance, AllAtOnce deployment, Health: Green |
| **EC2 Instance** | `i-02b5039d6b1e817a1` | t3.micro, public IP `16.58.151.186`, private `172.31.0.255` |
| **Launch Template** | `lt-01a32455066471fcc` | — |
| **Auto Scaling Group** | `awseb-e-2fcyjhgyu9-stack-AWSEBAutoScalingGroup-Y4sM5wLo1OMO` | Min: 1, Max: 1 |
| **SSH Key Pair** | `aws-eb` | SSH access open to `0.0.0.0/0` on port 22 |

### Database

| Resource | ID | Details |
|----------|----|---------|
| **RDS Instance** | `awseb-e-2fcyjhgyu9-stack-awsebrdsdatabase-7dafhgsyrlmy` | PostgreSQL 18.3, db.t3.micro, 5GB |
| **RDS Endpoint** | `*.cb46qw8cwu75.us-east-2.rds.amazonaws.com:5432` | Auto-injected via EB env vars |
| **Multi-AZ** | No | Single-AZ deployment |
| **DB User** | `ebroot` | Managed by EB |
| **DB Subnets** | `subnet-02d1ad820c19ca8d6`, `subnet-0e4ba09d65bb88c70`, `subnet-0b4991de35dfd28b0` | 3 AZs |

### Networking

| Resource | ID | Details |
|----------|----|---------|
| **VPC** | `vpc-0dbf831689e8a98b9` | — |
| **EC2 Security Group** | `sg-0d6444a7c5b4be99b` | SSH open to `0.0.0.0/0` |
| **EB CNAME** | `Chemically-env.eba-pyxp2kzs.us-east-2.elasticbeanstalk.com` | Default EB domain |
| **Custom Domain** | `chemic-ally.xyz` | Let's Encrypt SSL via certbot |

### Storage

| Resource | ID | Details |
|----------|----|---------|
| **App S3 Bucket** | `chemically-env` | Static/media files via django-storages |
| **EB Artifact Bucket** | `elasticbeanstalk-us-east-2-768125641662` | Deployment packages |
| **Cross-region EB Buckets** | `elasticbeanstalk-us-east-1-*`, `elasticbeanstalk-us-west-2-*` | Legacy/backup |

### IAM

| Resource | ARN | Purpose |
|----------|-----|---------|
| **EC2 Instance Profile** | `arn:aws:iam::768125641662:instance-profile/aws-elasticbeanstalk-ec2-role` | Attached to EC2, grants S3 access |
| **EC2 Role** | `arn:aws:iam::768125641662:role/service-role/aws-elasticbeanstalk-ec2-role` | Policies: WebTier, WorkerTier, MulticontainerDocker |
| **EB Service Role** | `arn:aws:iam::768125641662:role/service-role/aws-elasticbeanstalk-service-role` | EB platform management, managed updates |
| **Deploy IAM User** | `arn:aws:iam::768125641662:user/Franco` | Used by CI/CD (access key `...6W4O`) |

### Current Configuration

| Setting | Value | Notes |
|---------|-------|-------|
| **WSGI Path** | `config.wsgi:application` | — |
| **Gunicorn** | 1 process, 15 threads | — |
| **Proxy** | Nginx | — |
| **Deployment Policy** | `AllAtOnce` | Single instance, no rolling update |
| **Health Check URL** | *(empty)* | ⚠ Should be configured |
| **CloudWatch Logs** | Enabled, 7-day retention | Health streaming: **disabled** |
| **Managed Updates** | Enabled, Wed 10:25, minor level | — |
| **X-Ray** | Disabled | — |
| **Rollback on Failure** | Disabled | ⚠ Consider enabling |

---

## 3. Environment Variables & Secrets Matrix

### GitHub Actions Secrets

Configure in **GitHub → Repository → Settings → Secrets and variables → Actions**:

| Secret Name | Description | Used In | Required |
|-------------|-------------|---------|----------|
| `AWS_ACCESS_KEY_ID` | IAM user access key for EB deploy | Deploy job | Yes |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key for EB deploy | Deploy job | Yes |
| `AWS_ACCOUNT_ID` | AWS account number (`768125641662`) | Deploy job (S3 bucket name) | Yes |
| `DJANGO_SECRET_KEY` | Django cryptographic signing key | Build job (collectstatic) | Yes |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket for static files (`chemically-env`) | Build job (collectstatic) | Yes |

### EB Environment Properties

Configured in **AWS Console → EB → Configuration → Software → Environment properties**:

| Variable | Current Value | Managed By | Notes |
|----------|--------------|------------|-------|
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | `.ebextensions/django.config` | — |
| `SECRET_KEY` | *(set — plaintext visible)* | EB env prop | ⚠ Move to SSM Parameter Store |
| `AWS_ACCESS_KEY_ID` | `AKIA3FV63EO7CPKILX4C` *(set — plaintext visible)* | EB env prop | ⚠ Remove — instance profile has S3 access |
| `AWS_SECRET_ACCESS_KEY` | *(set — plaintext visible)* | EB env prop | ⚠ Remove — instance profile has S3 access |
| `AWS_STORAGE_BUCKET_NAME` | `chemically-env` | EB env prop | Correct |
| `AWS_S3_REGION_NAME` | `us-east-2` | EB env prop | Correct |
| `LETSENCRYPT_EMAIL` | `franco.v.navarro@gmail.com` | EB env prop | For certbot |
| `PYTHONPATH` | `/var/app/venv/staging-LQM1lest/bin` | EB env prop | ⚠ Venv path changes on deploy |

### RDS Variables (Auto-Injected by EB)

| Variable | Description | Notes |
|----------|-------------|-------|
| `RDS_DB_NAME` | PostgreSQL database name | Auto-injected when RDS attached |
| `RDS_USERNAME` | PostgreSQL username (`ebroot`) | Auto-injected |
| `RDS_PASSWORD` | PostgreSQL password | Auto-injected, masked in console |
| `RDS_HOSTNAME` | PostgreSQL endpoint | Auto-injected |
| `RDS_PORT` | PostgreSQL port (5432) | Auto-injected |

### OIDC Setup (Recommended Future Improvement)

No OIDC provider currently exists. To migrate from long-lived IAM keys:

1. **Create OIDC Identity Provider in AWS IAM:**
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`

2. **Create IAM Role** with trust policy:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "Federated": "arn:aws:iam::768125641662:oidc-provider/token.actions.githubusercontent.com"
         },
         "Action": "sts:AssumeRoleWithWebIdentity",
         "Condition": {
           "StringEquals": {
             "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
             "token.actions.githubusercontent.com:sub": "repo:<OWNER>/chemic-ally:ref:refs/heads/eb"
           }
         }
       }
     ]
   }
   ```

3. **Attach policy** to the role — minimum: `elasticbeanstalk:*`, `s3:PutObject`/`s3:GetObject` on EB artifact bucket.

4. **In GitHub Actions**, replace credential step with:
   ```yaml
   - uses: aws-actions/configure-aws-credentials@v4
     with:
       role-to-assume: arn:aws:iam::768125641662:role/ChemicAllyDeployRole
       aws-region: us-east-2
   ```

5. **Remove** `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from GitHub secrets.

---

## 4. Disaster Recovery & Rollback Protocols

### Immediate Rollback (< 2 minutes)

#### Option A: EB Console (Fastest)

1. Open **AWS Console → Elastic Beanstalk → Environments → Chemically-env**
2. Click **Environment actions → Rollback**
3. Select the **previous application version** (EB retains last 1000 versions)
4. Confirm — EB swaps to the previous version

#### Option B: AWS CLI

```bash
# List recent versions
aws elasticbeanstalk describe-application-versions \
  --application-name chemically \
  --region us-east-2 \
  --max-items 10

# Deploy previous version
aws elasticbeanstalk update-environment \
  --environment-name Chemically-env \
  --version-label <PREVIOUS_VERSION_LABEL> \
  --region us-east-2

# Wait for completion
aws elasticbeanstalk wait environment-updated \
  --environment-name Chemically-env
```

#### Option C: Git Revert

```bash
git checkout eb
git revert HEAD
git push origin eb
# CI pipeline redeploys the reverted code
```

### Database Migration Rollback

```bash
# SSH into the EB instance
eb ssh Chemically-env

# Activate venv and reverse the last migration
source /var/app/venv/*/bin/activate
cd /var/app/current
python manage.py migrate webapp <previous_migration_number>
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
| 3 | Verify rollback success — check `eb health` and smoke test | On-call |
| 4 | Create incident ticket with root cause analysis | Engineering |
| 5 | Fix issue in feature branch, re-run CI/CD pipeline | Engineering |
| 6 | Post-incident review and update runbook | Team |

---

## 5. Operational Procedures

### Deploying a New Version

**Automated (CI/CD):**
1. Merge code to `eb` branch
2. GitHub Actions pipeline: Lint → Test → Security Scan → Build → Deploy
3. Monitor: `eb health` or AWS Console

**Manual (fallback):**
```bash
git checkout eb
git merge main
eb deploy
```

### Checking Environment Health

```bash
# EB CLI
eb status
eb health

# AWS CLI — full health details
aws elasticbeanstalk describe-environment-health \
  --environment-name Chemically-env \
  --attribute-names All \
  --region us-east-2
```

### Viewing Application Logs

```bash
# Recent logs via EB CLI
eb logs

# SSH into instance for live logs
eb ssh Chemically-env
tail -f /var/log/web.stdout.log
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
tail -f /var/log/eb-activity.log
```

### Rotating Secrets

1. **Django SECRET_KEY:**
   - Generate: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
   - Update GitHub secret `DJANGO_SECRET_KEY`
   - Update EB environment property `SECRET_KEY`
   - Redeploy

2. **AWS Credentials (CI):**
   - Rotate IAM user keys in AWS IAM Console
   - Update GitHub secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   - No redeploy needed (credentials used only in CI)

3. **AWS Credentials (EB — recommended removal):**
   - The EC2 instance profile `aws-elasticbeanstalk-ec2-role` already has S3 access via `AWSElasticBeanstalkWebTier`
   - Remove `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from EB environment properties
   - Redeploy — django-storages will use instance profile credentials automatically

---

## 6. Security Hardening Recommendations

### Priority 1 — Remove Plaintext Secrets from EB

AWS credentials and Django SECRET_KEY are visible in plaintext via `describe-configuration-settings`.

**Fix:**
```bash
# Remove hardcoded AWS keys from EB (instance profile handles S3 access)
aws elasticbeanstalk update-environment \
  --environment-name Chemically-env \
  --option-settings file://remove-aws-keys.json \
  --region us-east-2
```

Where `remove-aws-keys.json`:
```json
{
  "OptionSettings": [
    {
      "Namespace": "aws:elasticbeanstalk:application:environment",
      "OptionName": "AWS_ACCESS_KEY_ID",
      "Value": ""
    },
    {
      "Namespace": "aws:elasticbeanstalk:application:environment",
      "OptionName": "AWS_SECRET_ACCESS_KEY",
      "Value": ""
    }
  ]
}
```

### Priority 2 — Restrict SSH Access

Current SSH access is open to `0.0.0.0/0`. Restrict to your IP:

```bash
aws elasticbeanstalk update-environment \
  --environment-name Chemically-env \
  --option-settings '[{
    "Namespace": "aws:autoscaling:launchconfiguration",
    "OptionName": "SSHSourceRestriction",
    "Value": "tcp,22,22,<YOUR_IP>/32"
  }]' \
  --region us-east-2
```

### Priority 3 — Configure Health Check URL

Currently empty. Set to monitor the application:

```bash
aws elasticbeanstalk update-environment \
  --environment-name Chemically-env \
  --option-settings '[{
    "Namespace": "aws:elasticbeanstalk:application",
    "OptionName": "Application Healthcheck URL",
    "Value": "HTTP:80/"
  }]' \
  --region us-east-2
```

### Priority 4 — Enable CloudWatch Health Streaming

Currently disabled. Enable for better observability:

```bash
aws elasticbeanstalk update-environment \
  --environment-name Chemically-env \
  --option-settings '[{
    "Namespace": "aws:elasticbeanstalk:cloudwatch:logs:health",
    "OptionName": "HealthStreamingEnabled",
    "Value": "true"
  }]' \
  --region us-east-2
```

### Priority 5 — Enable Rollback on Failure

Currently disabled. Enable automatic rollback if deployment fails:

```bash
aws elasticbeanstalk update-environment \
  --environment-name Chemically-env \
  --option-settings '[{
    "Namespace": "aws:elasticbeanstalk:control",
    "OptionName": "RollbackLaunchOnFailure",
    "Value": "true"
  }]' \
  --region us-east-2
```

---

## 7. Monitoring & Alerting

### Recommended CloudWatch Alarms

| Metric | Namespace | Threshold | Action |
|--------|-----------|-----------|--------|
| `HTTPCode_ELB_5XX_Count` | AWS/ApplicationELB | > 0 over 5 min | SNS alert |
| `EnvironmentHealth` | AWS/ElasticBeanstalk | Red | SNS alert + auto-rollback |
| `CPUUtilization` | AWS/EC2 | > 80% over 10 min | SNS alert |
| `DatabaseConnections` | AWS/RDS | > 80% of max | SNS alert |
| `FreeStorageSpace` | AWS/RDS | < 10% | SNS alert |
| `CPUUtilization` (RDS) | AWS/RDS | > 80% over 10 min | SNS alert |

### Health Check Endpoints

| Endpoint | Purpose | Expected Response |
|----------|---------|-------------------|
| `GET /` | Application availability | HTTP 200 |
| `GET /admin/` | Admin panel + database connectivity | HTTP 200 (or 302 redirect) |

---

*Last updated: 2026-05-17 | Maintained by: DevOps / Engineering Team*
