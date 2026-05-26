#!/usr/bin/env python3
"""
Export annotations for one round and archive the JSON to Wasabi exports/.

Env:
  LS_URL, LS_TOKEN
  WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET, WASABI_REGION (optional, default us-east-1)

Usage:
  python export_round.py \\
    --project-id 12 --batch-id batch_001 --round 1 \\
    --out exports/batch_001/round_1_export.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from label_studio_sdk import Client  # type: ignore
except ImportError:
    print("ERROR: pip install label-studio-sdk", file=sys.stderr)
    raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True, type=int)
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--round", dest="rnd", required=True, type=int)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--no-wasabi", action="store_true",
                    help="skip uploading the export to Wasabi (local copy only)")
    args = ap.parse_args()

    url = os.environ.get("LS_URL")
    token = os.environ.get("LS_TOKEN")
    if not url or not token:
        print("ERROR: set LS_URL and LS_TOKEN", file=sys.stderr)
        return 2

    ls = Client(url=url, api_key=token)
    project = ls.get_project(args.project_id)

    # JSON export includes all annotations + task data
    tasks = project.export_tasks(export_type="JSON")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tasks, indent=2))

    n_tasks = len(tasks)
    n_ann = sum(len(t.get("annotations", []) or []) for t in tasks)
    print(f"Exported {n_tasks} tasks, {n_ann} annotations to {args.out}")

    if args.no_wasabi:
        return 0

    access = os.environ.get("WASABI_ACCESS_KEY")
    secret = os.environ.get("WASABI_SECRET_KEY")
    bucket = os.environ.get("WASABI_BUCKET", "sknsi-annotation")
    region = os.environ.get("WASABI_REGION", "us-east-1")
    if not access or not secret:
        print("WARN: WASABI_ACCESS_KEY / WASABI_SECRET_KEY not set; "
              "skipping Wasabi upload", file=sys.stderr)
        return 0

    try:
        import boto3  # type: ignore
        from botocore.client import Config  # type: ignore
    except ImportError:
        print("ERROR: pip install boto3 (needed for Wasabi upload)", file=sys.stderr)
        return 2

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://s3.{region}.wasabisys.com",
        region_name=region,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
    )
    key = f"exports/{args.batch_id}/round_{args.rnd}_export.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=args.out.read_bytes(),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    print(f"Archived to s3://{bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
