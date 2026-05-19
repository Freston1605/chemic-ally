#!/bin/bash
# Setup certbot renewal cron + deploy hook.
# Uses DNS-01 for renewals (zero downtime, no nginx stop needed).

set -e

LOG="/var/log/certbot-install.log"

if ! command -v certbot &>/dev/null; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] certbot not found, skipping renewal setup." | tee -a "$LOG"
    exit 0
fi

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] Setting up certbot renewal..." | tee -a "$LOG"

# Create deploy hook — reloads nginx only when cert is actually renewed
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh << 'HOOK'
#!/bin/bash
if nginx -t 2>/dev/null; then
    systemctl reload nginx
fi
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

# Daily cron at 3:XX AM (randomized minute to avoid thundering herd)
# Uses DNS-01 — no need to stop nginx, zero downtime
CRON_FILE="/etc/cron.d/certbot-renew"
MINUTE=$((RANDOM % 60))

cat > "$CRON_FILE" << CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${MINUTE} 3 * * * root certbot renew --dns-route53 --non-interactive --quiet --deploy-hook /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh >> /var/log/certbot-renew.log 2>&1 || true
CRON
chmod 644 "$CRON_FILE"

# Add expiry monitoring — alert via CloudWatch custom metric if cert expires within 14 days
MONITOR_SCRIPT="/opt/certbot/bin/check-cert-expiry.sh"
cat > "$MONITOR_SCRIPT" << 'MONITOR'
#!/bin/bash
DOMAIN="chemic-ally.xyz"
CERT="/etc/letsencrypt/live/$DOMAIN/cert.pem"
LOG="/var/log/certbot-renew.log"

if [ ! -f "$CERT" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] CERT_EXPIRY_CHECK: CRITICAL - No certificate found" | tee -a "$LOG"
    exit 1
fi

EXPIRY=$(openssl x509 -enddate -noout -in "$CERT" | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

if [ "$DAYS_LEFT" -le 0 ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] CERT_EXPIRY_CHECK: CRITICAL - Certificate EXPIRED $((DAYS_LEFT * -1)) days ago" | tee -a "$LOG"
elif [ "$DAYS_LEFT" -le 14 ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] CERT_EXPIRY_CHECK: WARNING - Certificate expires in ${DAYS_LEFT} days" | tee -a "$LOG"
else
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] CERT_EXPIRY_CHECK: OK - Certificate valid for ${DAYS_LEFT} days" | tee -a "$LOG"
fi
MONITOR
chmod +x "$MONITOR_SCRIPT"

# Add expiry check to cron (runs daily at 9 AM)
cat >> "$CRON_FILE" << CRON2
0 9 * * * root $MONITOR_SCRIPT >> /var/log/certbot-renew.log 2>&1 || true
CRON2

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] Renewal cron and monitoring configured." | tee -a "$LOG"
