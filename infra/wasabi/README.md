# Phase 3 — Wasabi integration

Set up Wasabi as the canonical store for raw images, exports, masks, and training datasets.

## 1. Create bucket + folder layout

```bash
export AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY"

bash bucket_layout.sh sknsi-annotation us-east-1
```

Creates `sknsi-annotation/` with public-access blocked, versioning enabled, and all PRD §10.2 prefixes.

## 2. Create IAM policy + access keys

In Wasabi console:

1. **IAM → Policies → Create** → paste `iam_policy.json`
2. **IAM → Users → Create** user `sknsi-labelstudio`, attach the policy
3. Generate access keys → store in `infra/prod/.env` as `WASABI_ACCESS_KEY` / `WASABI_SECRET_KEY`

Never commit the keys. Rotate annually.

## 3. Upload first batch

```bash
pip install boto3 pillow

export WASABI_ACCESS_KEY=...
export WASABI_SECRET_KEY=...

python upload_batch.py \
  --src ./local_images/batch_001 \
  --batch-id batch_001 \
  --bucket sknsi-annotation \
  --region us-east-1 \
  --strip-exif \
  --out ../labeling/batch_001_round_1.json
```

Flags:

- `--strip-exif`: re-encodes images, removes EXIF/GPS metadata. **Required** unless source already de-identified.
- Outputs Label Studio import JSON with 7-day presigned URLs.

## 4. Connect Label Studio source storage (optional)

Instead of presigned URLs, configure LS source storage so it generates fresh URLs at task load:

In LS UI → **Project → Settings → Cloud Storage → Add Source Storage**:

- Storage type: `S3`
- Bucket: `sknsi-annotation`
- Prefix: `raw/batch_001/`
- Region: `us-east-1`
- S3 Endpoint: `https://s3.us-east-1.wasabisys.com`
- Access Key / Secret: from IAM user
- Treat every bucket object as a source file: yes
- Recursive scan: yes
- Use pre-signed URLs: yes
- Pre-signed URL counter: `3600` (seconds)

Then **Sync Storage** to pull tasks into the project.

For exports, add **Target Storage** with prefix `exports/batch_001/`.

## 5. De-identification checklist (run before upload)

Per PRD §19.2, images must not contain:

- [ ] Face
- [ ] Name / DOB / MRN visible
- [ ] Phone / address visible
- [ ] Hospital wristband
- [ ] Document labels
- [ ] GPS EXIF (auto-stripped by `--strip-exif`)
- [ ] Identifying tattoos (if avoidable)

Document who reviewed each batch before upload.

## 6. Backup automation

The Phase 2 `deploy.sh` installs `/etc/cron.d/sknsi-backup` automatically:

- **Daily 03:00** — `pg_dump` → gzip to `/opt/sknsi-annotate/backups/`, then `aws s3 sync` to `s3://sknsi-annotation/backups/postgres/`.
- **Weekly Sunday 04:00** — local prune of dumps older than 30 days.

The cron reads `WASABI_ACCESS_KEY`, `WASABI_SECRET_KEY`, `WASABI_BUCKET`, `WASABI_ENDPOINT` from `/opt/sknsi-annotate/.env`. Fill these before the first cron run, or backups will be created locally but won't sync.

Verify after 24h:

```bash
docker compose exec -T postgres pg_isready && \
  ls -la /opt/sknsi-annotate/backups/ && \
  AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
    aws --endpoint-url "$WASABI_ENDPOINT" s3 ls "s3://$WASABI_BUCKET/backups/postgres/"
```
