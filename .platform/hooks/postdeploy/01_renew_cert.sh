#!/bin/bash
# ---------------------------------------------------------------------------
# Post-deploy hook: attempt cert renewal on every deployment
# This runs after the application has been fully deployed.  If the certificate
# is due for renewal (>60 days old), certbot renew will update it and reload
# nginx automatically.
#
# Amazon Linux 2023 / Elastic Beanstalk
# ---------------------------------------------------------------------------
set -e

# Only proceed if certbot is installed
if ! command -v certbot &>/dev/null; then
    echo "certbot not installed; skipping renewal."
    exit 0
fi

# Only proceed if we have an existing certificate
if [ ! -d "/etc/letsencrypt/live/chemic-ally.xyz" ]; then
    echo "No existing certificate found; skipping renewal."
    exit 0
fi

echo "Attempting certificate renewal..."
certbot renew --non-interactive --quiet \
    --deploy-hook "systemctl reload nginx" 2>&1 \
    | logger -t certbot-postdeploy || true

echo "Certificate renewal check complete."