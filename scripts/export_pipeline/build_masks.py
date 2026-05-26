#!/usr/bin/env python3
"""
Render PNG label masks from Maher v1.0 annotations.

Two outputs per image:

  <out>/<image_id>_mask.png     -- multi-class brush mask
                                    0 = background
                                    1 = Erythema (primary affected)
                                    2 = Streaking / lymphangitis
                                    3 = Bullae / blister
                                    4 = Necrosis / black tissue
                                    5 = Ulceration / open wound
                                    6 = Drainage / pus
                                    7 = Normal comparison
  <out>/<image_id>_lesion.png   -- binary lesion-boundary mask (1 inside polygon)

Only images where consensus.include_in_training == True are emitted. The
brush source annotator is taken from consensus.final_brush_source_annotator;
polygons from consensus.final_polygon_source_annotator. When brushes from
two classes overlap on the same pixel, CLASS_PRIORITY decides the winner.

Brush RLE decoding requires label-studio-sdk (preferred) or label-studio-converter.

Usage:
  pip install label-studio-sdk pillow numpy
  python build_masks.py \\
    --parsed parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \\
    --consensus consensus/batch_001_consensus.csv \\
    --images-dir local/raw/batch_001 \\
    --out-dir training/v1/masks
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

try:
    from label_studio_sdk.converter.brush import decode_rle  # newer SDK
except ImportError:
    try:
        from label_studio_converter.brush import decode_rle  # legacy
    except ImportError:
        decode_rle = None


BRUSH_CLASS_INDEX = {
    "Erythema (primary affected)":  1,
    "Streaking / lymphangitis":     2,
    "Bullae / blister":             3,
    "Necrosis / black tissue":      4,
    "Ulceration / open wound":      5,
    "Drainage / pus":               6,
    "Normal comparison":            7,
}

# When two brushes overlap, paint LATER classes on top. Lower priority first.
CLASS_PAINT_ORDER = [
    "Normal comparison",
    "Erythema (primary affected)",
    "Streaking / lymphangitis",
    "Bullae / blister",
    "Drainage / pus",
    "Ulceration / open wound",
    "Necrosis / black tissue",   # highest priority (overrides everything)
]
EXTS = (".jpg", ".jpeg", ".png")


def load_parsed(paths: list[Path]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for p in paths:
        with p.open() as f:
            for r in csv.DictReader(f):
                out[(r["image_id"], r.get("annotator_id", ""))] = r
    return out


def find_image(images_dir: Path, image_id: str) -> Path | None:
    for ext in EXTS:
        c = images_dir / f"{image_id}{ext}"
        if c.exists():
            return c
    return None


def decode_brush_to_alpha(rle: list[int], h: int, w: int) -> np.ndarray | None:
    """Decode an LS brush RLE into a binary (h, w) bool array using the alpha channel."""
    if not rle or decode_rle is None or h <= 0 or w <= 0:
        return None
    flat = decode_rle(rle)
    arr = np.frombuffer(flat, dtype=np.uint8) if isinstance(flat, (bytes, bytearray)) else np.asarray(flat, dtype=np.uint8)
    expected = h * w * 4
    if arr.size != expected:
        # RLE corrupt or canvas dims wrong
        return None
    rgba = arr.reshape(h, w, 4)
    return rgba[:, :, 3] > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", nargs="+", required=True, type=Path)
    ap.add_argument("--consensus", required=True, type=Path)
    ap.add_argument("--images-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()

    if decode_rle is None:
        print(
            "ERROR: install label-studio-sdk (or label-studio-converter) before running build_masks.py.\n"
            "       pip install label-studio-sdk",
            file=sys.stderr,
        )
        return 2

    parsed = load_parsed(args.parsed)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    n_brush = n_polygon = skipped = 0
    with args.consensus.open() as f:
        for row in csv.DictReader(f):
            if row.get("include_in_training", "").lower() not in {"true", "1"}:
                continue
            image_id = row["image_id"]
            img_path = find_image(args.images_dir, image_id)
            if not img_path:
                skipped += 1
                continue
            with Image.open(img_path) as im:
                w, h = im.size

            # Brush mask from final_brush_source_annotator
            brush_ann = parsed.get((image_id, row.get("final_brush_source_annotator", "")))
            mask = np.zeros((h, w), dtype=np.uint8)
            if brush_ann:
                try:
                    brushes = json.loads(brush_ann.get("brush_regions_json") or "[]")
                except json.JSONDecodeError:
                    brushes = []
                # Sort brushes by paint order so high-priority classes overwrite lower.
                priority = {name: i for i, name in enumerate(CLASS_PAINT_ORDER)}
                brushes.sort(key=lambda b: priority.get(b.get("label") or "", -1))
                for br in brushes:
                    cls = BRUSH_CLASS_INDEX.get(br.get("label") or "")
                    if not cls:
                        continue
                    bh = br.get("original_height") or h
                    bw = br.get("original_width") or w
                    alpha = decode_brush_to_alpha(br.get("rle") or [], bh, bw)
                    if alpha is None:
                        continue
                    if (bh, bw) != (h, w):
                        # Resize alpha to image dims using nearest neighbor
                        alpha_img = Image.fromarray(alpha.astype(np.uint8) * 255, mode="L")
                        alpha_img = alpha_img.resize((w, h), Image.NEAREST)
                        alpha = np.array(alpha_img) > 0
                    mask[alpha] = cls
            if mask.any():
                Image.fromarray(mask, mode="L").save(args.out_dir / f"{image_id}_mask.png")
                n_brush += 1

            # Binary lesion-boundary mask from final_polygon_source_annotator
            poly_ann = parsed.get((image_id, row.get("final_polygon_source_annotator", "")))
            if poly_ann:
                try:
                    polys = json.loads(poly_ann.get("polygons_json") or "[]")
                except json.JSONDecodeError:
                    polys = []
                if polys:
                    lesion = Image.new("L", (w, h), 0)
                    draw = ImageDraw.Draw(lesion)
                    for poly in polys:
                        pts_pct = poly.get("points") or []
                        pts = [(p[0] / 100.0 * w, p[1] / 100.0 * h) for p in pts_pct]
                        if len(pts) >= 3:
                            draw.polygon(pts, outline=1, fill=1)
                    lesion.save(args.out_dir / f"{image_id}_lesion.png")
                    n_polygon += 1

    print(f"Wrote {n_brush} brush masks, {n_polygon} lesion masks (skipped {skipped})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
