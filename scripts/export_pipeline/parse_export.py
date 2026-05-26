#!/usr/bin/env python3
"""
Parse a Label Studio JSON export for the Maher v1.0 schema
(infra/labeling/template.xml).

One row per annotation. Per-lesion (perRegion) fields collapsed into JSON columns
since each image may have multiple polygons. Brush regions retain full RLE so
build_masks.py can reconstruct multi-class label masks.

Usage:
  python parse_export.py --in export.json --out parsed.csv --batch-id batch_001
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
def _by_name(result: list[dict]) -> dict[str, list[dict]]:
    """Group result entries by from_name. Some controls fire multiple times
       (per-region) so values are lists."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in result:
        out[r.get("from_name", "")].append(r)
    return out


def first_choice(entries: list[dict]) -> str:
    for r in entries:
        if r["type"] == "choices":
            v = r["value"].get("choices") or []
            return v[0] if v else ""
    return ""


def all_choices(entries: list[dict]) -> list[str]:
    for r in entries:
        if r["type"] == "choices":
            return list(r["value"].get("choices") or [])
    return []


def rating(entries: list[dict]) -> int | None:
    for r in entries:
        if r["type"] == "rating":
            return int(r["value"].get("rating") or 0)
    return None


def textarea(entries: list[dict]) -> str:
    for r in entries:
        if r["type"] == "textarea":
            t = r["value"].get("text") or []
            return " ".join(t) if isinstance(t, list) else str(t)
    return ""


def polygons(result: list[dict]) -> list[dict]:
    """Return list of polygon regions: {id, label, points}."""
    out = []
    for r in result:
        if r["type"] == "polygonlabels":
            out.append({
                "id": r.get("id"),
                "label": (r["value"].get("polygonlabels") or [None])[0],
                "points": r["value"].get("points") or [],
            })
    return out


def brush_regions(result: list[dict]) -> list[dict]:
    """Return brush regions with full RLE so masks can be reconstructed downstream."""
    out = []
    for r in result:
        if r["type"] == "brushlabels":
            v = r["value"]
            out.append({
                "id": r.get("id"),
                "label": (v.get("brushlabels") or [None])[0],
                "format": v.get("format", "rle"),
                "rle": v.get("rle") or [],
                "original_width": r.get("original_width"),
                "original_height": r.get("original_height"),
            })
    return out


def per_region(result: list[dict], from_name: str, parent_ids: set[str]) -> dict[str, Any]:
    """Map polygon region id -> selected value(s) for the given perRegion control."""
    out: dict[str, Any] = {}
    for r in result:
        if r.get("from_name") != from_name:
            continue
        # perRegion entries have a parent_id pointing at the region they belong to
        pid = r.get("parent_id") or r.get("region_id") or r.get("from_id")
        if pid not in parent_ids:
            continue
        if r["type"] == "choices":
            vals = r["value"].get("choices") or []
            out[pid] = vals if r["value"].get("choice") == "multiple" else (vals[0] if vals else "")
    return out


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────
PER_LESION_SINGLE = [
    "interior_exclusions", "border_definition", "lesion_extent",
    "erythema_color", "erythema_uniformity", "hemosiderin", "yellow_tones",
    "edema", "red_flag",
]
PER_LESION_MULTI = ["surface_texture", "lesion_features"]


COLS = [
    # task / annotation identity
    "annotation_id", "task_id", "image_id", "batch_id",
    "annotator_id", "review_round", "submitted_at",
    # section 0
    "annotation_status", "skip_reason",
    # section A
    "image_modality", "lighting", "image_quality", "image_quality_reason",
    "scale_reference", "framing",
    # section B
    "anatomic_region", "laterality", "contralateral_visible", "patient_position",
    # section C
    "pen_marks", "pen_colors", "written_annotations",
    "surface_artifacts", "preexisting_features", "privacy_issues",
    # section D
    "diagnosis", "confirmation_status", "fitzpatrick", "coexisting_findings",
    # section F
    "diagnosis_confidence", "segmentation_confidence",
    "needs_second_opinion", "notes",
    # geometry
    "polygon_count", "brush_count",
    "polygons_json", "brush_regions_json",
    # per-lesion attributes flattened by polygon id
    "per_lesion_attrs_json",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--batch-id", default="")
    args = ap.parse_args()

    tasks = json.loads(args.inp.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)

    n_rows = 0
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()

        for task in tasks:
            data = task.get("data", {})
            image_id = data.get("image_id") or Path(data.get("image", "")).stem
            batch_id = data.get("batch_id") or args.batch_id

            for ann in task.get("annotations", []):
                result = ann.get("result", []) or []
                by = _by_name(result)

                polys = polygons(result)
                brushes = brush_regions(result)
                poly_ids = {p["id"] for p in polys if p.get("id")}

                per_lesion: dict[str, dict[str, Any]] = defaultdict(dict)
                for fn in PER_LESION_SINGLE + PER_LESION_MULTI:
                    pr = per_region(result, fn, poly_ids)
                    for pid, val in pr.items():
                        per_lesion[pid][fn] = val

                row = {
                    # identity
                    "annotation_id": ann.get("id"),
                    "task_id": task.get("id"),
                    "image_id": image_id,
                    "batch_id": batch_id,
                    "annotator_id": ann.get("completed_by"),
                    "review_round": data.get("review_round", ""),
                    "submitted_at": ann.get("created_at", ""),
                    # 0
                    "annotation_status": first_choice(by.get("annotation_status", [])),
                    "skip_reason": textarea(by.get("skip_reason", [])),
                    # A
                    "image_modality": first_choice(by.get("image_modality", [])),
                    "lighting": first_choice(by.get("lighting", [])),
                    "image_quality": first_choice(by.get("image_quality", [])),
                    "image_quality_reason": "|".join(all_choices(by.get("image_quality_reason", []))),
                    "scale_reference": first_choice(by.get("scale_reference", [])),
                    "framing": first_choice(by.get("framing", [])),
                    # B
                    "anatomic_region": first_choice(by.get("anatomic_region", [])),
                    "laterality": first_choice(by.get("laterality", [])),
                    "contralateral_visible": first_choice(by.get("contralateral_visible", [])),
                    "patient_position": first_choice(by.get("patient_position", [])),
                    # C
                    "pen_marks": first_choice(by.get("pen_marks", [])),
                    "pen_colors": "|".join(all_choices(by.get("pen_colors", []))),
                    "written_annotations": first_choice(by.get("written_annotations", [])),
                    "surface_artifacts": "|".join(all_choices(by.get("surface_artifacts", []))),
                    "preexisting_features": "|".join(all_choices(by.get("preexisting_features", []))),
                    "privacy_issues": "|".join(all_choices(by.get("privacy_issues", []))),
                    # D
                    "diagnosis": first_choice(by.get("diagnosis", [])),
                    "confirmation_status": first_choice(by.get("confirmation_status", [])),
                    "fitzpatrick": first_choice(by.get("fitzpatrick", [])),
                    "coexisting_findings": "|".join(all_choices(by.get("coexisting_findings", []))),
                    # F
                    "diagnosis_confidence": rating(by.get("diagnosis_confidence", [])) or "",
                    "segmentation_confidence": rating(by.get("segmentation_confidence", [])) or "",
                    "needs_second_opinion": first_choice(by.get("needs_second_opinion", [])),
                    "notes": textarea(by.get("notes", [])),
                    # geometry
                    "polygon_count": len(polys),
                    "brush_count": len(brushes),
                    "polygons_json": json.dumps(polys),
                    "brush_regions_json": json.dumps(brushes),
                    # per-lesion attributes
                    "per_lesion_attrs_json": json.dumps(per_lesion),
                }
                w.writerow(row)
                n_rows += 1

    print(f"Parsed {n_rows} annotation row(s) → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
