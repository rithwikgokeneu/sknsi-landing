#!/usr/bin/env python3
"""
Package the final training dataset under training/v<version>/ from Maher v1.0 outputs.

Layout:
  training/v1/
    images/                  raw input images (copied from --raw-dir)
    masks/                   brush + lesion masks (copied from --masks-dir)
      <image_id>_mask.png    multi-class brush mask
      <image_id>_lesion.png  binary polygon mask (optional)
    labels.csv               image_id, final_diagnosis
    metadata.csv             full per-image metadata
    per_lesion.csv           one row per polygon: per-lesion attributes
    README.md                dataset card

Usage:
  python package_dataset.py \\
    --consensus consensus/batch_001_consensus.csv \\
    --parsed parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \\
    --raw-dir local/raw \\
    --masks-dir training/v1/masks \\
    --out-dir training/v1
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

EXTS = (".jpg", ".jpeg", ".png")

META_COLS = [
    "image_id", "batch_id",
    "final_diagnosis", "diagnosis_agreement_status",
    "final_confirmation_status", "final_fitzpatrick",
    "reviewer_count", "include_in_training",
    "min_diagnosis_confidence", "min_segmentation_confidence",
    "has_emergency_red_flag",
    # technical context (from first parsed row for the image)
    "image_modality", "lighting", "image_quality",
    "scale_reference", "framing",
    # anatomic context
    "anatomic_region", "laterality", "contralateral_visible", "patient_position",
    # surface confounds
    "pen_marks", "surface_artifacts", "preexisting_features", "privacy_issues",
    "coexisting_findings",
]

PER_LESION_COLS = [
    "image_id", "batch_id", "polygon_id", "polygon_label", "annotator_id",
    "interior_exclusions", "border_definition", "lesion_extent",
    "erythema_color", "erythema_uniformity", "hemosiderin", "yellow_tones",
    "edema", "red_flag",
    "surface_texture", "lesion_features",  # multi-select; pipe-joined
]


def find_image(d: Path, image_id: str) -> Path | None:
    for ext in EXTS:
        c = d / f"{image_id}{ext}"
        if c.exists():
            return c
        for sub in d.rglob(f"{image_id}{ext}"):
            return sub
    return None


def index_parsed(paths: list[Path]) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    """Return (first-seen-by-image, by (image_id, annotator_id))."""
    by_image: dict[str, dict] = {}
    by_pair: dict[tuple[str, str], dict] = {}
    for p in paths:
        with p.open() as f:
            for r in csv.DictReader(f):
                by_image.setdefault(r["image_id"], r)
                by_pair[(r["image_id"], r.get("annotator_id", ""))] = r
    return by_image, by_pair


def stringify(val) -> str:
    if isinstance(val, list):
        return "|".join(str(v) for v in val)
    return "" if val is None else str(val)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--consensus", required=True, type=Path)
    ap.add_argument("--parsed", nargs="+", required=True, type=Path)
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--masks-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()

    by_image, by_pair = index_parsed(args.parsed)
    images_out = args.out_dir / "images"
    masks_out = args.out_dir / "masks"
    images_out.mkdir(parents=True, exist_ok=True)
    masks_out.mkdir(parents=True, exist_ok=True)

    labels_csv = args.out_dir / "labels.csv"
    meta_csv = args.out_dir / "metadata.csv"
    per_lesion_csv = args.out_dir / "per_lesion.csv"

    lw = csv.writer(labels_csv.open("w", newline=""))
    lw.writerow(["image_id", "final_diagnosis"])
    mw = csv.DictWriter(meta_csv.open("w", newline=""), fieldnames=META_COLS)
    mw.writeheader()
    pw = csv.DictWriter(per_lesion_csv.open("w", newline=""), fieldnames=PER_LESION_COLS)
    pw.writeheader()

    n_inc = n_skip = n_per_lesion = 0
    with args.consensus.open() as f:
        for row in csv.DictReader(f):
            image_id = row["image_id"]
            if row.get("include_in_training", "").lower() not in {"true", "1"}:
                n_skip += 1
                continue
            src = find_image(args.raw_dir, image_id)
            if not src:
                n_skip += 1
                continue
            shutil.copy2(src, images_out / src.name)

            brush_mask = args.masks_dir / f"{image_id}_mask.png"
            if brush_mask.exists():
                shutil.copy2(brush_mask, masks_out / brush_mask.name)
            lesion_mask = args.masks_dir / f"{image_id}_lesion.png"
            if lesion_mask.exists():
                shutil.copy2(lesion_mask, masks_out / lesion_mask.name)

            lw.writerow([image_id, row.get("final_diagnosis", "")])

            extra = by_image.get(image_id, {})
            mw.writerow({
                "image_id": image_id,
                "batch_id": row.get("batch_id", ""),
                "final_diagnosis": row.get("final_diagnosis", ""),
                "diagnosis_agreement_status": row.get("diagnosis_agreement_status", ""),
                "final_confirmation_status": row.get("final_confirmation_status", ""),
                "final_fitzpatrick": row.get("final_fitzpatrick", ""),
                "reviewer_count": row.get("reviewer_count", ""),
                "include_in_training": "true",
                "min_diagnosis_confidence": row.get("min_diagnosis_confidence", ""),
                "min_segmentation_confidence": row.get("min_segmentation_confidence", ""),
                "has_emergency_red_flag": row.get("has_emergency_red_flag", ""),
                "image_modality": extra.get("image_modality", ""),
                "lighting": extra.get("lighting", ""),
                "image_quality": extra.get("image_quality", ""),
                "scale_reference": extra.get("scale_reference", ""),
                "framing": extra.get("framing", ""),
                "anatomic_region": extra.get("anatomic_region", ""),
                "laterality": extra.get("laterality", ""),
                "contralateral_visible": extra.get("contralateral_visible", ""),
                "patient_position": extra.get("patient_position", ""),
                "pen_marks": extra.get("pen_marks", ""),
                "surface_artifacts": extra.get("surface_artifacts", ""),
                "preexisting_features": extra.get("preexisting_features", ""),
                "privacy_issues": extra.get("privacy_issues", ""),
                "coexisting_findings": extra.get("coexisting_findings", ""),
            })
            n_inc += 1

            # Per-lesion rows from the polygon-source annotator
            poly_annotator = row.get("final_polygon_source_annotator", "")
            poly_row = by_pair.get((image_id, poly_annotator))
            if not poly_row:
                continue
            try:
                polys = json.loads(poly_row.get("polygons_json") or "[]")
            except json.JSONDecodeError:
                polys = []
            try:
                per_lesion = json.loads(poly_row.get("per_lesion_attrs_json") or "{}")
            except json.JSONDecodeError:
                per_lesion = {}
            for poly in polys:
                pid = poly.get("id") or ""
                attrs = per_lesion.get(pid, {})
                pw.writerow({
                    "image_id": image_id,
                    "batch_id": row.get("batch_id", ""),
                    "polygon_id": pid,
                    "polygon_label": poly.get("label", ""),
                    "annotator_id": poly_annotator,
                    "interior_exclusions": stringify(attrs.get("interior_exclusions")),
                    "border_definition": stringify(attrs.get("border_definition")),
                    "lesion_extent": stringify(attrs.get("lesion_extent")),
                    "erythema_color": stringify(attrs.get("erythema_color")),
                    "erythema_uniformity": stringify(attrs.get("erythema_uniformity")),
                    "hemosiderin": stringify(attrs.get("hemosiderin")),
                    "yellow_tones": stringify(attrs.get("yellow_tones")),
                    "edema": stringify(attrs.get("edema")),
                    "red_flag": stringify(attrs.get("red_flag")),
                    "surface_texture": stringify(attrs.get("surface_texture")),
                    "lesion_features": stringify(attrs.get("lesion_features")),
                })
                n_per_lesion += 1

    (args.out_dir / "README.md").write_text(
        "# Training dataset (Maher v1.0)\n\n"
        f"- Included images: {n_inc}\n"
        f"- Skipped: {n_skip}\n"
        f"- Per-lesion rows: {n_per_lesion}\n\n"
        "## Files\n\n"
        "- `images/` raw input images\n"
        "- `masks/<image_id>_mask.png` multi-class brush label mask\n"
        "    - 0 = background\n"
        "    - 1 = Erythema (primary affected)\n"
        "    - 2 = Streaking / lymphangitis\n"
        "    - 3 = Bullae / blister\n"
        "    - 4 = Necrosis / black tissue\n"
        "    - 5 = Ulceration / open wound\n"
        "    - 6 = Drainage / pus\n"
        "    - 7 = Normal comparison\n"
        "- `masks/<image_id>_lesion.png` binary lesion-boundary mask (0/1)\n"
        "- `labels.csv` image_id -> final_diagnosis\n"
        "- `metadata.csv` per-image metadata (technical + anatomic context)\n"
        "- `per_lesion.csv` per-polygon attributes (color, extent, edema, red_flag, etc.)\n"
        "- `annotations_coco.json` COCO segmentation (run build_coco.py separately)\n"
    )
    print(f"Packaged {n_inc} images, {n_per_lesion} per-lesion rows to {args.out_dir} "
          f"(skipped {n_skip})")
    print("Next: run build_coco.py, then sync to Wasabi:")
    print(f"  aws --endpoint-url https://s3.us-east-1.wasabisys.com s3 sync {args.out_dir} "
          f"s3://sknsi-annotation/training/v1/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
