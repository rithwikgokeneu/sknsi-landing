# Phase 2 — Hostinger VPS deployment

Deploys Label Studio + Postgres behind Nginx with HTTPS, served at `https://sknsi.com/annotate`.

## Requirements

- Hostinger **VPS** (not shared hosting). Ubuntu 22.04+ recommended.
- DNS A records: `sknsi.com` and `www.sknsi.com` pointing to VPS IP. **Must resolve before running `deploy.sh`** — the script aborts if DNS doesn't match the VPS public IP.
- Static site files placed at `/var/www/sknsi/` (e.g. `index.html`, `annotate.html`, `images/`).
- SSH access as root or sudo user.
- Phase 1 sign-off passed (`infra/local/README.md` checklist).

## One-time deploy

On your laptop:

```bash
# from repo root
rsync -av --delete infra/prod/ root@VPS_IP:/opt/sknsi-annotate/
rsync -av index.html annotate.html images/ root@VPS_IP:/var/www/sknsi/
```

On the VPS:

```bash
cd /opt/sknsi-annotate
cp .env.example .env
nano .env                  # set strong POSTGRES_PASSWORD, LABEL_STUDIO_PASSWORD
chmod 600 .env

bash deploy.sh             # installs Docker, Nginx, Certbot, brings up containers
```

`deploy.sh` runs in 9 steps:

1. apt update/upgrade
2. Install Docker, Nginx, Certbot, dnsutils
3. UFW firewall (SSH + Nginx Full)
4. Create app dirs, enforce `.env` chmod 600
5. **DNS sanity check** — abort if `sknsi.com` / `www.sknsi.com` don't resolve to this VPS
6. Install HTTP-only bootstrap nginx config (serves `/.well-known/acme-challenge/`)
7. Obtain TLS cert via `certbot certonly --webroot`
8. Swap in production nginx config (HTTPS + reverse proxy)
9. Bring up Postgres + Label Studio containers; install daily backup cron; print Certbot renewal timer status

### URL routing

LS runs under `/annotate` via Django `FORCE_SCRIPT_NAME=/annotate`. All LS-generated URLs (HTML, `/annotate/static/...`, `/annotate/api/...`, `/annotate/ws/...`) are nested under that prefix, so the single nginx `location /annotate` block proxies everything.

### Migrating Phase 1 data (optional)

If you want to keep the project + annotations created during Phase 1 local validation:

```bash
# On laptop — dump local DB before tearing down Phase 1 stack
docker compose -f infra/local/docker-compose.yml exec -T postgres \
  pg_dump -U labelstudio labelstudio | gzip > /tmp/phase1.sql.gz

scp /tmp/phase1.sql.gz root@VPS_IP:/tmp/

# On VPS — restore into prod DB BEFORE first LS start, OR stop LS, wipe ls_data,
# restore, then start LS so it re-uses existing project
cd /opt/sknsi-annotate
docker compose stop labelstudio
gunzip < /tmp/phase1.sql.gz | docker compose exec -T postgres \
  psql -U "$POSTGRES_USER" "$POSTGRES_DB"
docker compose start labelstudio
```

Otherwise: skip — re-create the project on prod LS by pasting `infra/labeling/template.xml` again.

## Verify

```bash
curl -I https://sknsi.com/                # 200, static
curl -I https://sknsi.com/annotate.html   # 200, info page
curl -I https://sknsi.com/annotate/       # 200/302, Label Studio
```

Open `https://sknsi.com/annotate` in a browser. Login with admin from `.env`.

## Daily ops

```bash
cd /opt/sknsi-annotate
docker compose logs -f labelstudio        # live logs
docker compose restart labelstudio        # restart app
docker compose pull && docker compose up -d   # update LS image

# Postgres backup
docker compose exec postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "/opt/sknsi-annotate/backups/pg_$(date +%F).sql.gz"
```

Daily Postgres backup at 03:00 is installed automatically by `deploy.sh` (`/etc/cron.d/sknsi-backup`). Add a separate cron entry to rsync `backups/` to Wasabi `backups/postgres/` (see `infra/wasabi/README.md` §6).

## Security checklist

- [ ] `.env` chmod 600, owner root
- [ ] SSH key-only login, password auth disabled
- [ ] UFW: only 22, 80, 443 open
- [ ] `LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true` set
- [ ] Strong admin password rotated quarterly
- [ ] Postgres port not exposed (only on `sknsi-net`)
- [ ] Label Studio port bound to loopback only (`127.0.0.1:8080`), unreachable from public internet
- [ ] Nginx serves HSTS, X-Frame-Options, X-Content-Type-Options
- [ ] Certbot auto-renew confirmed: `systemctl list-timers | grep certbot`

## Rollback

```bash
docker compose down                       # stop app
docker compose up -d --force-recreate     # reboot from current images
# DB rollback: gunzip < backups/pg_YYYY-MM-DD.sql.gz | docker compose exec -T postgres psql -U $POSTGRES_USER $POSTGRES_DB
```
