#!/usr/bin/env bash
# Initialize Wasabi bucket folder layout per PRD §10.2.
# Wasabi is S3-compatible; uses aws-cli with --endpoint-url.
#
# Prereqs:
#   aws --version          # >= 2.x
#   export AWS_ACCESS_KEY_ID=$WASABI_ACCESS_KEY
#   export AWS_SECRET_ACCESS_KEY=$WASABI_SECRET_KEY
#
# Usage:
#   ./bucket_layout.sh sknsi-annotation us-east-1
set -euo pipefail

BUCKET="${1:-sknsi-annotation}"
REGION="${2:-us-east-1}"
ENDPOINT="https://s3.${REGION}.wasabisys.com"

aws_w() { aws --endpoint-url "$ENDPOINT" --region "$REGION" "$@"; }

echo "Creating bucket s3://$BUCKET in $REGION (skip if exists)"
aws_w s3api create-bucket --bucket "$BUCKET" 2>/dev/null || true

echo "Block public access"
aws_w s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
  || true

echo "Enable versioning (recovery from accidental delete)"
aws_w s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

echo "Creating folder placeholders"
PREFIXES=(
  "raw/"
  "imports/"
  "exports/"
  "disagreements/"
  "consensus/"
  "masks/v1/"
  "training/v1/images/"
  "training/v1/masks/"
  "backups/postgres/"
  "backups/labelstudio/"
)
for p in "${PREFIXES[@]}"; do
  aws_w s3api put-object --bucket "$BUCKET" --key "$p" >/dev/null
  echo "  + $p"
done

echo "Done. Verify:"
echo "  aws --endpoint-url $ENDPOINT s3 ls s3://$BUCKET/"
