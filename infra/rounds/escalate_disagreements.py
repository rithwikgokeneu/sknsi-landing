#!/usr/bin/env python3
"""
Build a Round-3 LS import JSON containing only the escalated images.

Reads disagreements.csv from Phase 5 agreement.py, filters the original
batch tasks JSON down to those image_ids, and writes a new tasks file
ready for create_round_project.py --round 3.

Per-task data is augmented with:
  review_round: "3"
  escalation_reasons: from disagreements.csv

Usage:
  python escalate_disagreements.py \\
    --batch-id batch_001 \\
    --disagreements ../../scripts/export_pipeline/disagreements/batch_001_disagreements.csv \\
    --tasks-source ../labeling/batch_001_round_1.json \\
    --out ../labeling/batch_001_round_3.json
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--disagreements", required=True, type=Path)
    ap.add_argument("--tasks-source", required=True, type=Path,
                    help="original round-1 LS import JSON (has presigned URLs)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    reasons_by_image: dict[str, str] = {}
    with args.disagreements.open() as f:
        for row in csv.DictReader(f):
            reasons_by_image[row["image_id"]] = row.get("reasons", "")

    if not reasons_by_image:
        print("No disagreements to escalate. Skipping.")
        args.out.write_text("[]\n")
        return 0

    src = json.loads(args.tasks_source.read_text())
    escalated = []
    for task in src:
        data = task.get("data", {})
        image_id = data.get("image_id")
        if image_id not in reasons_by_image:
            continue
        new_data = dict(data)
        new_data["review_round"] = "3"
        new_data["escalation_reasons"] = reasons_by_image[image_id]
        escalated.append({"data": new_data})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(escalated, indent=2))
    print(f"Wrote {len(escalated)} escalated tasks to {args.out}")
    print(f"Next: python create_round_project.py --batch-id {args.batch_id} "
          f"--round 3 --tasks {args.out} --template ../labeling/template.xml "
          f"--assignees dermatologist@hospital.org")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
