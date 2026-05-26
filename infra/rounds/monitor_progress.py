#!/usr/bin/env python3
"""
Print completion stats for one LS project.

Env: LS_URL, LS_TOKEN

Usage:
  python monitor_progress.py --project-id 12
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

try:
    from label_studio_sdk import Client  # type: ignore
except ImportError:
    print("ERROR: pip install label-studio-sdk", file=sys.stderr)
    raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True, type=int)
    args = ap.parse_args()

    url = os.environ.get("LS_URL")
    token = os.environ.get("LS_TOKEN")
    if not url or not token:
        print("ERROR: set LS_URL and LS_TOKEN", file=sys.stderr)
        return 2

    ls = Client(url=url, api_key=token)
    project = ls.get_project(args.project_id)

    total = project.get_params().get("task_number", 0)
    finished = project.get_params().get("finished_task_number", 0)
    pct = (finished / total * 100.0) if total else 0.0

    print(f"Project {args.project_id}: {project.title}")
    print(f"  Tasks: {finished} / {total}  ({pct:.1f}%)")

    # Per-annotator breakdown
    tasks = project.get_tasks()
    by_user: Counter = Counter()
    for t in tasks:
        for ann in t.get("annotations", []) or []:
            uid = ann.get("completed_by")
            if uid:
                by_user[str(uid)] += 1
    if by_user:
        print("  Annotations per user_id:")
        for uid, n in by_user.most_common():
            print(f"    {uid}: {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
