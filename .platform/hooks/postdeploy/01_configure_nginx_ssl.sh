#!/bin/bash
# Configure nginx SSL for HTTPS after EB platform setup completes.
# This runs as a postdeploy hook, so EB's nginx config is already in place.

set -e

DOMAIN="chemic-ally.xyz"
WWW_DOMAIN="www.chemic-ally.xyz"
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
NGINX_CONF="/etc/nginx/conf.d/00_https_redirect.conf"
LOG="/var/log/certbot-install.log"

if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] No certificate found. Removing custom SSL config." | tee -a "$LOG"
    rm -f "$NGINX_CONF"
    exit 0
fi

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] Writing nginx SSL configuration..." | tee -a "$LOG"

cat > "$NGINX_CONF" << 'NGINX_HTTP'
server {
    listen 80;
    listen [::]:80;
    server_name chemic-ally.xyz www.chemic-ally.xyz;

    location /.well-known/acme-challenge/ {
        root /var/lib/letsencrypt;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
NGINX_HTTP

cat >> "$NGINX_CONF" << NGINX_HTTPS
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name chemic-ally.xyz www.chemic-ally.xyz;

    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_trusted_certificate ${CERT_DIR}/chain.pem;

    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 8.8.8.8 1.1.1.1 valid=300s;
    resolver_timeout 5s;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
NGINX_HTTPS

if nginx -t 2>/dev/null; then
    if systemctl reload nginx; then
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] nginx reloaded successfully." | tee -a "$LOG"
    else
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] WARNING: nginx config valid, but reload failed." | tee -a "$LOG"
    fi
else
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [postdeploy] ERROR: nginx config test failed." | tee -a "$LOG"
    exit 1
fi
