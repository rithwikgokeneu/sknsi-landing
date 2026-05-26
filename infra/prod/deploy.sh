#!/usr/bin/env bash
# Run on Hostinger VPS as root or via sudo. Idempotent.
set -euo pipefail

DOMAIN="sknsi.com"
EMAIL="admin@sknsi.com"     # Let's Encrypt registration
APP_DIR="/opt/sknsi-annotate"
WEB_ROOT="/var/www/sknsi"
ACME_ROOT="/var/www/certbot"

echo "[1/9] System update"
apt-get update -y
apt-get upgrade -y

echo "[2/9] Install Docker, Compose plugin, Nginx, Certbot, dnsutils"
apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx ufw dnsutils
install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
fi
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[3/9] Firewall"
ufw allow OpenSSH
ufw allow 'Nginx Full'
yes | ufw enable || true

echo "[4/9] App dirs + .env"
mkdir -p "$APP_DIR" "$WEB_ROOT" "$ACME_ROOT" "$APP_DIR/backups"
if [ ! -f "$APP_DIR/.env" ]; then
  echo "MISSING $APP_DIR/.env — copy .env.example and fill values"; exit 1
fi
chmod 600 "$APP_DIR/.env"

echo "[5/9] DNS sanity check (must point to this VPS before Certbot)"
VPS_IP=$(curl -fsS https://ifconfig.me || curl -fsS https://api.ipify.org)
for host in "$DOMAIN" "www.$DOMAIN"; do
  DNS_IP=$(dig +short A "$host" | tail -n1)
  if [ -z "$DNS_IP" ] || [ "$VPS_IP" != "$DNS_IP" ]; then
    echo "DNS mismatch: $host → '$DNS_IP', VPS public IP=$VPS_IP"
    echo "Fix DNS A records at registrar, wait propagation, re-run."
    exit 1
  fi
done
echo "  DNS OK: $DOMAIN, www.$DOMAIN → $VPS_IP"

echo "[6/9] Nginx HTTP-only stub (so Certbot --webroot can serve ACME challenge)"
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-available/sknsi-bootstrap.conf <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $DOMAIN www.$DOMAIN;

    location /.well-known/acme-challenge/ {
        root $ACME_ROOT;
    }

    location / {
        return 200 "bootstrap-ok\n";
        add_header Content-Type text/plain;
    }
}
EOF
ln -sf /etc/nginx/sites-available/sknsi-bootstrap.conf /etc/nginx/sites-enabled/sknsi-bootstrap.conf
rm -f /etc/nginx/sites-enabled/sknsi.conf
nginx -t
systemctl reload nginx

echo "[7/9] Obtain TLS cert via Certbot (webroot)"
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  certbot certonly --webroot -w "$ACME_ROOT" \
    --non-interactive --agree-tos -m "$EMAIL" \
    -d "$DOMAIN" -d "www.$DOMAIN"
else
  echo "  Cert already exists, skipping issuance."
fi

echo "[8/9] Swap in production Nginx config (HTTPS + reverse proxy)"
install -m 0644 "$APP_DIR/nginx/sknsi.conf" /etc/nginx/sites-available/sknsi.conf
ln -sf /etc/nginx/sites-available/sknsi.conf /etc/nginx/sites-enabled/sknsi.conf
rm -f /etc/nginx/sites-enabled/sknsi-bootstrap.conf
nginx -t
systemctl reload nginx

echo "[9/9] Bring up Label Studio + Postgres"
cd "$APP_DIR"
docker compose pull
docker compose up -d
docker compose ps

echo "Installing daily Postgres backup cron + Wasabi sync"
# AWS CLI required for the Wasabi sync step.
if ! command -v aws >/dev/null 2>&1; then
  apt-get install -y awscli
fi
cat > /etc/cron.d/sknsi-backup <<'EOF'
# Daily Postgres dump at 03:00, then sync backups/ to Wasabi.
# Both Wasabi keys + Postgres creds are sourced from /opt/sknsi-annotate/.env.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * root cd /opt/sknsi-annotate && set -a && . ./.env && set +a && docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "/opt/sknsi-annotate/backups/pg_$(date +\%F).sql.gz" && AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" aws --endpoint-url "$WASABI_ENDPOINT" s3 sync /opt/sknsi-annotate/backups/ "s3://$WASABI_BUCKET/backups/postgres/"
# Weekly local prune — keep last 30 days
0 4 * * 0 root find /opt/sknsi-annotate/backups -name 'pg_*.sql.gz' -mtime +30 -delete
EOF
chmod 644 /etc/cron.d/sknsi-backup

echo
echo "Certbot auto-renew timer:"
systemctl list-timers --no-pager | grep -E "certbot|snap.certbot" || echo "  WARN: no certbot timer detected — check 'systemctl status certbot.timer'"

cat <<EOF

Done. Verify:
  curl -I https://$DOMAIN/
  curl -I https://$DOMAIN/annotate.html
  curl -I https://$DOMAIN/annotate/
Logs:
  docker compose -f $APP_DIR/docker-compose.yml logs -f labelstudio
EOF
