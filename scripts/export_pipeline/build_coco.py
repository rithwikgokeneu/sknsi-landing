#!/usr/bin/env python3
"""
Build COCO-format segmentation JSON from Maher v1.0 annotations.

Categories: 7 brush classes + 1 lesion boundary polygon category.
Each connected component of each brush mask becomes one COCO annotation
with RLE segmentation (pycocotools format). Lesion polygons emit polygon
segmentation.

Usage:
  pip install label-studio-sdk pycocotools pillow numpy scipy
  python build_coco.py \\
    --parsed parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \\
    --consensus consensus/batch_001_consensus.csv \\
    --images-dir training/v1/images \\
    --out training/v1/annotations_coco.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from label_studio_sdk.converter.brush import decode_rle
except ImportError:
    try:
        from label_studio_converter.brush import decode_rle
    except ImportError:
        decode_rle = None

try:
    from pycocotools import mask as maskUtils  # type: ignore
except ImportError:
    maskUtils = None

try:
    from scipy.ndimage import label as cc_label  # type: ignore
except ImportError:
    cc_label = None


CATEGORIES = [
    {"id": 1, "name": "Erythema (primary affected)"},
    {"id": 2, "name": "Streaking / lymphangitis"},
    {"id": 3, "name": "Bullae / blister"},
    {"id": 4, "name": "Necrosis / black tissue"},
    {"id": 5, "name": "Ulceration / open wound"},
    {"id": 6, "name": "Drainage / pus"},
    {"id": 7, "name": "Normal comparison"},
    {"id": 8, "name": "Lesion boundary"},
]
NAME_TO_ID = {c["name"]: c["id"] for c in CATEGORIES}
EXTS = (".jpg", ".jpeg", ".png")


def find_image(images_dir: Path, image_id: str) -> Path | None:
    for ext in EXTS:
        c = images_dir / f"{image_id}{ext}"
        if c.exists():
            return c
    return None


def decode_brush_to_alpha(rle: list[int], h: int, w: int) -> np.ndarray | None:
    if not rle or decode_rle is None or h <= 0 or w <= 0:
        return None
    flat = decode_rle(rle)
    arr = np.frombuffer(flat, dtype=np.uint8) if isinstance(flat, (bytes, bytearray)) else np.asarray(flat, dtype=np.uint8)
    if arr.size != h * w * 4:
        return None
    return arr.reshape(h, w, 4)[:, :, 3] > 0


def polygon_pts_to_coco(points_pct: list[list[float]], w: int, h: int) -> tuple[list[float], list[float], float]:
    flat: list[float] = []
    xs: list[float] = []
    ys: list[float] = []
    for p in points_pct:
        x = p[0] / 100.0 * w
        y = p[1] / 100.0 * h
        flat.extend([x, y])
        xs.append(x)
        ys.append(y)
    if not xs:
        return flat, [0, 0, 0, 0], 0.0
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    bbox = [x0, y0, x1 - x0, y1 - y0]
    n = len(xs)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += xs[i] * ys[j] - xs[j] * ys[i]
    return flat, bbox, abs(area) / 2.0


def encode_component(component: np.ndarray) -> tuple[dict, list[float], float]:
    """Encode a binary component as COCO RLE + bbox + area."""
    rle = maskUtils.encode(np.asfortranarray(component.astype(np.uint8)))
    rle["counts"] = rle["counts"].decode("ascii")  # JSON-safe
    ys, xs = np.where(component)
    if xs.size == 0:
        return rle, [0, 0, 0, 0], 0.0
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    bbox = [float(x0), float(y0), float(x1 - x0 + 1), float(y1 - y0 + 1)]
    return rle, bbox, float(component.sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", nargs="+", required=True, type=Path)
    ap.add_argument("--consensus", required=True, type=Path)
    ap.add_argument("--images-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    missing = []
    if decode_rle is None:
        missing.append("label-studio-sdk")
    if maskUtils is None:
        missing.append("pycocotools")
    if cc_label is None:
        missing.append("scipy")
    if missing:
        print(f"ERROR: install missing deps: pip install {' '.join(missing)}", file=sys.stderr)
        return 2

    parsed: dict[tuple[str, str], dict] = {}
    for p in args.parsed:
        with p.open() as f:
            for r in csv.DictReader(f):
                parsed[(r["image_id"], r.get("annotator_id", ""))] = r

    images: list[dict] = []
    annotations: list[dict] = []
    img_id_seq = 0
    ann_id_seq = 0

    with args.consensus.open() as f:
        for row in csv.DictReader(f):
            if row.get("include_in_training", "").lower() not in {"true", "1"}:
                continue
            image_id = row["image_id"]
            img_path = find_image(args.images_dir, image_id)
            if not img_path:
                continue
            with Image.open(img_path) as im:
                w, h = im.size

            img_id_seq += 1
            images.append({
                "id": img_id_seq,
                "file_name": img_path.name,
                "width": w,
                "height": h,
                "image_id": image_id,
                "batch_id": row.get("batch_id", ""),
            })

            # Brush components per class
            brush_ann = parsed.get((image_id, row.get("final_brush_source_annotator", "")))
            if brush_ann:
                try:
                    brushes = json.loads(brush_ann.get("brush_regions_json") or "[]")
                except json.JSONDecodeError:
                    brushes = []
                # Union per-class masks across all regions of that class
                class_masks: dict[int, np.ndarray] = {}
                for br in brushes:
                    cls = NAME_TO_ID.get(br.get("label") or "")
                    if not cls:
                        continue
                    bh = br.get("original_height") or h
                    bw = br.get("original_width") or w
                    alpha = decode_brush_to_alpha(br.get("rle") or [], bh, bw)
                    if alpha is None:
                        continue
                    if (bh, bw) != (h, w):
                        alpha_img = Image.fromarray(alpha.astype(np.uint8) * 255, mode="L")
                        alpha_img = alpha_img.resize((w, h), Image.NEAREST)
                        alpha = np.array(alpha_img) > 0
                    if cls in class_masks:
                        class_masks[cls] |= alpha
                    else:
                        class_masks[cls] = alpha.copy()

                for cls, m in class_masks.items():
                    labeled, n_comp = cc_label(m)
                    for k in range(1, n_comp + 1):
                        comp = labeled == k
                        if not comp.any():
                            continue
                        seg_rle, bbox, area = encode_component(comp)
                        if area <= 0:
                            continue
                        ann_id_seq += 1
                        annotations.append({
                            "id": ann_id_seq,
                            "image_id": img_id_seq,
                            "category_id": cls,
                            "segmentation": seg_rle,
                            "bbox": bbox,
                            "area": area,
                            "iscrowd": 0,
                        })

            # Lesion boundary polygons
            poly_ann = parsed.get((image_id, row.get("final_polygon_source_annotator", "")))
            if poly_ann:
                try:
                    polys = json.loads(poly_ann.get("polygons_json") or "[]")
                except json.JSONDecodeError:
                    polys = []
                lesion_cat = NAME_TO_ID["Lesion boundary"]
                for poly in polys:
                    seg, bbox, area = polygon_pts_to_coco(poly.get("points") or [], w, h)
                    if not seg or area <= 0:
                        continue
                    ann_id_seq += 1
                    annotations.append({
                        "id": ann_id_seq,
                        "image_id": img_id_seq,
                        "category_id": lesion_cat,
                        "segmentation": [seg],
                        "bbox": bbox,
                        "area": area,
                        "iscrowd": 0,
                    })

    coco = {
        "info": {"description": "SKNSI Maher v1.0 cellulitis annotations", "version": "1.0"},
        "images": images,
        "annotations": annotations,
        "categories": CATEGORIES,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(coco))
    print(f"Wrote {args.out}: {len(images)} images, {len(annotations)} annotations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
