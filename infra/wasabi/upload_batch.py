#!/usr/bin/env python3
"""
Upload a local image batch to Wasabi and emit a Label Studio import JSON.

Usage:
  export WASABI_ACCESS_KEY=...
  export WASABI_SECRET_KEY=...
  python upload_batch.py \
      --src ./local_images/batch_001 \
      --batch-id batch_001 \
      --bucket sknsi-annotation \
      --region us-east-1 \
      --strip-exif \
      --out ../labeling/batch_001_round_1.json

Requires: pip install boto3 pillow
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

import boto3
from botocore.client import Config
from PIL import Image

EXTS = {".jpg", ".jpeg", ".png"}


def strip_exif(path: Path) -> bytes:
    img = Image.open(path)
    if img.mode in ("P", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    fmt = "JPEG" if path.suffix.lower() in {".jpg", ".jpeg"} else "PNG"
    img.save(buf, format=fmt, quality=92, optimize=True)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path, help="local folder of images")
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--bucket", default="sknsi-annotation")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--strip-exif", action="store_true")
    ap.add_argument("--out", required=True, type=Path, help="output LS import JSON")
    args = ap.parse_args()

    endpoint = args.endpoint or f"https://s3.{args.region}.wasabisys.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=args.region,
        aws_access_key_id=os.environ["WASABI_ACCESS_KEY"],
        aws_secret_access_key=os.environ["WASABI_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
    )

    if not args.src.is_dir():
        print(f"src not a dir: {args.src}", file=sys.stderr)
        return 1

    tasks: list[dict] = []
    files = sorted(p for p in args.src.iterdir() if p.suffix.lower() in EXTS)
    if not files:
        print("no images found", file=sys.stderr)
        return 1

    for i, p in enumerate(files, start=1):
        image_id = f"case_{i:06d}"
        key = f"raw/{args.batch_id}/{image_id}{p.suffix.lower()}"
        body = strip_exif(p) if args.strip_exif else p.read_bytes()
        s3.put_object(
            Bucket=args.bucket,
            Key=key,
            Body=body,
            ContentType="image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ServerSideEncryption="AES256",
        )
        # presigned URL valid 7 days for LS task data
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": args.bucket, "Key": key},
            ExpiresIn=7 * 24 * 3600,
        )
        tasks.append({
            "data": {
                "image": url,
                "image_id": image_id,
                "batch_id": args.batch_id,
                "s3_key": key,
            }
        })
        print(f"  uploaded {key}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tasks, indent=2))
    print(f"\nWrote {len(tasks)} tasks to {args.out}")

    # Also archive the import manifest to imports/<batch_id>/<basename>.json
    import_key = f"imports/{args.batch_id}/{args.out.name}"
    s3.put_object(
        Bucket=args.bucket,
        Key=import_key,
        Body=args.out.read_bytes(),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    print(f"Archived manifest to s3://{args.bucket}/{import_key}")

    print("Import this file in Label Studio: Project -> Import")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
