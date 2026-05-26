#!/usr/bin/env python3
"""
Create a Label Studio project for one annotation round.

Creates the project with the Maher v1.0 label config, imports the batch
tasks JSON, and assigns the listed annotators. Idempotent on project
title — re-running with the same --batch-id / --round won't create a
duplicate; it adds tasks + assignees to the existing project.

Env: LS_URL, LS_TOKEN

Usage:
  python create_round_project.py \\
    --batch-id batch_001 --round 1 \\
    --template ../labeling/template.xml \\
    --tasks ../labeling/batch_001_round_1.json \\
    --assignees alice@hospital.org bob@hospital.org
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


def project_title(batch_id: str, rnd: int) -> str:
    return f"SKNSI Pilot — {batch_id} Round {rnd}"


def find_or_create_project(ls, title: str, label_config: str):
    for p in ls.list_projects():
        if p.title == title:
            print(f"  reusing existing project id={p.id}")
            return p
    p = ls.start_project(title=title, label_config=label_config)
    print(f"  created project id={p.id}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--round", dest="rnd", required=True, type=int)
    ap.add_argument("--template", required=True, type=Path)
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--assignees", nargs="*", default=[],
                    help="annotator emails to invite + assign")
    args = ap.parse_args()

    url = os.environ.get("LS_URL")
    token = os.environ.get("LS_TOKEN")
    if not url or not token:
        print("ERROR: set LS_URL and LS_TOKEN", file=sys.stderr)
        return 2

    label_config = args.template.read_text()
    tasks = json.loads(args.tasks.read_text())

    ls = Client(url=url, api_key=token)
    ls.check_connection()

    title = project_title(args.batch_id, args.rnd)
    print(f"Project: {title}")
    project = find_or_create_project(ls, title, label_config)

    print(f"Importing {len(tasks)} tasks...")
    imported = project.import_tasks(tasks)
    print(f"  imported task ids: {imported[:3]}{'...' if len(imported) > 3 else ''} "
          f"(total {len(imported)})")

    # Invite + assign annotators via REST. The SDK has no first-class
    # "assign user to project" helper, so we hit the API directly.
    if args.assignees:
        print(f"Assigning {len(args.assignees)} annotator(s)...")
        for email in args.assignees:
            # Create invite (idempotent — server returns existing on duplicate email)
            invite = ls.make_request(
                "POST", "/api/invites/",
                json={"email": email, "role": "AN"},
            )
            link = (invite.json() if hasattr(invite, "json") else invite).get("link", "")
            print(f"  {email}  invite: {link or '(already member)'}")
            # Attach to project's members
            try:
                ls.make_request(
                    "POST",
                    f"/api/projects/{project.id}/members/",
                    json={"user_email": email},
                )
            except Exception as e:  # pragma: no cover — LS version dependent
                print(f"    WARN: could not auto-assign {email} -> {e}")

    print(f"\nProject URL: {url.rstrip('/')}/projects/{project.id}/data")
    print(f"Project id:  {project.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
