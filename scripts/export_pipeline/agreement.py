#!/usr/bin/env python3
"""
Compute reviewer agreement and emit a consensus CSV + disagreement list
against the Maher v1.0 schema produced by parse_export.py.

Reads parsed annotation CSV (one row per annotation). Groups by image_id,
applies PRD §14.3 escalation rules adapted to Maher v1.0 controls.

Outputs:
  consensus.csv      - one row per image with final fields
  disagreements.csv  - subset needing dermatologist review

Escalation rules (any triggers needs_dermatologist_review=True):
  - Diagnosis vote split (no majority)
  - Confirmation status disagrees
  - Any annotator flagged needs_second_opinion = Yes
  - Any per-lesion red_flag = "Yes — emergency review"
  - Any annotator: annotation_status != "Annotate (proceed)"
  - min(diagnosis_confidence) <= 2
  - min(segmentation_confidence) <= 2
  - Any annotator image_quality = "Unusable"

Usage:
  python agreement.py \\
    --in parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \\
    --consensus consensus/batch_001_consensus.csv \\
    --disagreements disagreements/batch_001_disagreements.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ANNOTATE_STATUS = "Annotate (proceed)"
EMERGENCY_RED_FLAG = "Yes — emergency review"
UNUSABLE_QUALITY = "Unusable"
LOW_CONFIDENCE_THRESHOLD = 2  # ratings 1 or 2 escalate


def load_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        with p.open() as f:
            rows.extend(list(csv.DictReader(f)))
    return rows


def majority(values: list[str]) -> tuple[str, str]:
    """Return (winner, status) where status in {agreement, majority, split}."""
    cleaned = [v for v in values if v]
    if not cleaned:
        return "", "split"
    counts: dict[str, int] = defaultdict(int)
    for v in cleaned:
        counts[v] += 1
    top = max(counts.values())
    winners = [k for k, v in counts.items() if v == top]
    if len(winners) == 1 and top == len(cleaned):
        return winners[0], "agreement"
    if len(winners) == 1:
        return winners[0], "majority"
    return winners[0], "split"


def safe_int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def any_per_lesion_red_flag(per_lesion_json: str) -> bool:
    """per_lesion_attrs_json maps polygon_id -> {field: value}."""
    try:
        per_lesion = json.loads(per_lesion_json or "{}")
    except json.JSONDecodeError:
        return False
    for attrs in per_lesion.values():
        if attrs.get("red_flag") == EMERGENCY_RED_FLAG:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", nargs="+", required=True, type=Path)
    ap.add_argument("--consensus", required=True, type=Path)
    ap.add_argument("--disagreements", required=True, type=Path)
    args = ap.parse_args()

    rows = load_rows(args.inp)
    by_image: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_image[r["image_id"]].append(r)

    consensus_cols = [
        "image_id", "batch_id", "reviewer_count",
        "final_diagnosis", "diagnosis_agreement_status",
        "final_confirmation_status", "confirmation_agreement_status",
        "final_fitzpatrick",
        "final_polygon_source_annotator", "final_brush_source_annotator",
        "min_diagnosis_confidence", "min_segmentation_confidence",
        "has_emergency_red_flag", "any_unusable_image",
        "any_skip_or_exclude", "any_needs_second_opinion",
        "needs_dermatologist_review", "include_in_training",
    ]
    disagree_cols = [
        "image_id", "batch_id", "reviewers", "diagnosis_values",
        "confirmation_values", "min_diagnosis_confidence",
        "min_segmentation_confidence", "reasons",
    ]
    args.consensus.parent.mkdir(parents=True, exist_ok=True)
    args.disagreements.parent.mkdir(parents=True, exist_ok=True)

    cw = csv.DictWriter(args.consensus.open("w", newline=""), fieldnames=consensus_cols)
    dw = csv.DictWriter(args.disagreements.open("w", newline=""), fieldnames=disagree_cols)
    cw.writeheader()
    dw.writeheader()

    n_total = n_escalated = n_included = 0
    for image_id, anns in by_image.items():
        n_total += 1

        diagnoses = [a.get("diagnosis", "") for a in anns]
        confirmations = [a.get("confirmation_status", "") for a in anns]
        fitz = [a.get("fitzpatrick", "") for a in anns]
        diag_confs = [safe_int(a.get("diagnosis_confidence", "")) for a in anns]
        seg_confs = [safe_int(a.get("segmentation_confidence", "")) for a in anns]
        diag_confs_nz = [c for c in diag_confs if c > 0]
        seg_confs_nz = [c for c in seg_confs if c > 0]

        winner_diag, status_diag = majority(diagnoses)
        winner_conf, status_conf = majority(confirmations)
        winner_fitz, _ = majority(fitz)

        min_diag_conf = min(diag_confs_nz) if diag_confs_nz else 0
        min_seg_conf = min(seg_confs_nz) if seg_confs_nz else 0

        has_red_flag = any(any_per_lesion_red_flag(a.get("per_lesion_attrs_json", "")) for a in anns)
        any_unusable = any(a.get("image_quality") == UNUSABLE_QUALITY for a in anns)
        any_skip = any(a.get("annotation_status", "") and a["annotation_status"] != ANNOTATE_STATUS for a in anns)
        any_need_2nd = any(a.get("needs_second_opinion") == "Yes" for a in anns)

        reasons = []
        if status_diag == "split":
            reasons.append("split_diagnosis_vote")
        if status_conf == "split":
            reasons.append("split_confirmation_vote")
        if any_need_2nd:
            reasons.append("needs_second_opinion")
        if has_red_flag:
            reasons.append("emergency_red_flag")
        if any_skip:
            reasons.append("annotation_status_not_annotate")
        if min_diag_conf and min_diag_conf <= LOW_CONFIDENCE_THRESHOLD:
            reasons.append(f"low_diagnosis_confidence={min_diag_conf}")
        if min_seg_conf and min_seg_conf <= LOW_CONFIDENCE_THRESHOLD:
            reasons.append(f"low_segmentation_confidence={min_seg_conf}")
        if any_unusable:
            reasons.append("image_quality_unusable")

        needs_review = bool(reasons)

        # Pick polygon + brush source = highest segmentation_confidence annotator
        # with non-empty regions of that type.
        poly_source = ""
        brush_source = ""
        best_poly_conf = -1
        best_brush_conf = -1
        for a in anns:
            seg_c = safe_int(a.get("segmentation_confidence", ""))
            try:
                polys = json.loads(a.get("polygons_json") or "[]")
            except json.JSONDecodeError:
                polys = []
            try:
                brushes = json.loads(a.get("brush_regions_json") or "[]")
            except json.JSONDecodeError:
                brushes = []
            if polys and seg_c > best_poly_conf:
                best_poly_conf = seg_c
                poly_source = a.get("annotator_id", "")
            if brushes and seg_c > best_brush_conf:
                best_brush_conf = seg_c
                brush_source = a.get("annotator_id", "")

        include = (not needs_review) and (not any_unusable) and (not any_skip)
        if include:
            n_included += 1

        cw.writerow({
            "image_id": image_id,
            "batch_id": anns[0].get("batch_id", ""),
            "reviewer_count": len(anns),
            "final_diagnosis": winner_diag,
            "diagnosis_agreement_status": status_diag,
            "final_confirmation_status": winner_conf,
            "confirmation_agreement_status": status_conf,
            "final_fitzpatrick": winner_fitz,
            "final_polygon_source_annotator": poly_source,
            "final_brush_source_annotator": brush_source,
            "min_diagnosis_confidence": min_diag_conf,
            "min_segmentation_confidence": min_seg_conf,
            "has_emergency_red_flag": has_red_flag,
            "any_unusable_image": any_unusable,
            "any_skip_or_exclude": any_skip,
            "any_needs_second_opinion": any_need_2nd,
            "needs_dermatologist_review": needs_review,
            "include_in_training": include,
        })

        if needs_review:
            n_escalated += 1
            dw.writerow({
                "image_id": image_id,
                "batch_id": anns[0].get("batch_id", ""),
                "reviewers": "|".join(a.get("annotator_id", "") for a in anns),
                "diagnosis_values": "|".join(diagnoses),
                "confirmation_values": "|".join(confirmations),
                "min_diagnosis_confidence": min_diag_conf,
                "min_segmentation_confidence": min_seg_conf,
                "reasons": ";".join(reasons),
            })

    print(f"Images: {n_total}  Included: {n_included}  Escalated: {n_escalated}  "
          f"({n_escalated/max(n_total,1):.1%})")
    print(f"  consensus -> {args.consensus}")
    print(f"  disagreements -> {args.disagreements}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
