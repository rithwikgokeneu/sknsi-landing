# Phase 5 — Export & training-dataset pipeline (Maher v1.0)

Turns Label Studio JSON exports of the Maher v1.0 schema
(`infra/labeling/template.xml`) into a versioned, ML-ready dataset under
`training/v1/`.

## Install

```bash
pip install pillow numpy scipy label-studio-sdk pycocotools boto3
```

`label-studio-sdk` provides `decode_rle` for brush masks. `pycocotools` is only
needed by `build_coco.py`. `boto3` is only needed for the final Wasabi sync.

## Pipeline

```
LS export JSON
   │
   ▼  parse_export.py
parsed CSV (one row per annotation; brush RLE + per-lesion attrs in JSON cols)
   │
   ▼  agreement.py
consensus.csv  +  disagreements.csv
   │
   ├──▶ build_masks.py        ──▶ masks/<image_id>_{mask,lesion}.png
   ├──▶ build_coco.py         ──▶ annotations_coco.json
   └──▶ package_dataset.py    ──▶ training/v1/{images,masks,labels.csv,metadata.csv,per_lesion.csv,README.md}
                                  └─▶ aws s3 sync ─▶ Wasabi training/v1/
```

## Run

```bash
# 1. Parse each round
python parse_export.py --in round_1_export.json --out parsed/batch_001_round_1.csv --batch-id batch_001
python parse_export.py --in round_2_export.json --out parsed/batch_001_round_2.csv --batch-id batch_001

# 2. Agreement + escalation (v2 rules — see below)
python agreement.py \
  --in parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \
  --consensus consensus/batch_001_consensus.csv \
  --disagreements disagreements/batch_001_disagreements.csv

# 3. Send disagreements to a Dermatologist Review project in LS, export round_3.
#    Re-run parse_export.py + agreement.py including the round_3 CSV.

# 4. Build masks (brush multi-class + lesion polygon)
python build_masks.py \
  --parsed parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \
  --consensus consensus/batch_001_consensus.csv \
  --images-dir local/raw/batch_001 \
  --out-dir training/v1/masks

# 5. Package dataset (copies images + masks, emits labels.csv + metadata.csv + per_lesion.csv)
python package_dataset.py \
  --consensus consensus/batch_001_consensus.csv \
  --parsed parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \
  --raw-dir local/raw \
  --masks-dir training/v1/masks \
  --out-dir training/v1

# 6. COCO segmentation (RLE for brush components + polygon for lesion boundary)
python build_coco.py \
  --parsed parsed/batch_001_round_1.csv parsed/batch_001_round_2.csv \
  --consensus consensus/batch_001_consensus.csv \
  --images-dir training/v1/images \
  --out training/v1/annotations_coco.json

# 7. Sync to Wasabi
AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
  aws --endpoint-url https://s3.us-east-1.wasabisys.com \
      s3 sync training/v1/ s3://sknsi-annotation/training/v1/
```

## v2 escalation rules (PRD §14.3 adapted to Maher v1.0)

`agreement.py` escalates an image when any of:

- `diagnosis` vote is split (no single majority value)
- `confirmation_status` vote is split
- Any annotator: `needs_second_opinion == "Yes"`
- Any per-lesion `red_flag == "Yes — emergency review"`
- Any annotator: `annotation_status != "Annotate (proceed)"` (skip / exclude / privacy / duplicate)
- `min(diagnosis_confidence) <= 2`
- `min(segmentation_confidence) <= 2`
- Any annotator: `image_quality == "Unusable"`

`disagreements.csv` lists `reasons` per image — route those to a Dermatologist
Review project in LS for round 3.

## Output schema

### `consensus.csv` (one row per image)

`image_id, batch_id, reviewer_count, final_diagnosis, diagnosis_agreement_status,
final_confirmation_status, confirmation_agreement_status, final_fitzpatrick,
final_polygon_source_annotator, final_brush_source_annotator,
min_diagnosis_confidence, min_segmentation_confidence, has_emergency_red_flag,
any_unusable_image, any_skip_or_exclude, any_needs_second_opinion,
needs_dermatologist_review, include_in_training`

### `training/v1/masks/<image_id>_mask.png` (multi-class brush mask)

| value | class |
|------|-------|
| 0 | background |
| 1 | Erythema (primary affected) |
| 2 | Streaking / lymphangitis |
| 3 | Bullae / blister |
| 4 | Necrosis / black tissue |
| 5 | Ulceration / open wound |
| 6 | Drainage / pus |
| 7 | Normal comparison |

When brush regions of two classes overlap on the same pixel, the higher
priority class wins (Necrosis > Ulceration > Drainage > Bullae > Streaking >
Erythema > Normal). See `CLASS_PAINT_ORDER` in `build_masks.py`.

### `training/v1/masks/<image_id>_lesion.png` (binary lesion-boundary mask)

`0 = background, 1 = inside polygon`. Polygon is the consensus annotator's
`lesion_boundary` PolygonLabels region(s).

### `training/v1/per_lesion.csv` (one row per polygon)

`image_id, batch_id, polygon_id, polygon_label, annotator_id,
interior_exclusions, border_definition, lesion_extent,
erythema_color, erythema_uniformity, hemosiderin, yellow_tones,
edema, red_flag, surface_texture, lesion_features`

`surface_texture` and `lesion_features` are multi-select — pipe-joined.

## Polygon coords note

Label Studio polygon `points` are normalized **0–100** relative to image
dimensions. `build_masks.py` and `build_coco.py` convert to pixels using
PIL-loaded `(width, height)`.

## Brush RLE note

Label Studio brush regions are stored as RLE (run-length-encoded RGBA bytes)
at the original image resolution. `parse_export.py` preserves the raw RLE
plus `original_width` / `original_height`. Downstream scripts decode via
`label-studio-sdk.converter.brush.decode_rle` (or legacy
`label-studio-converter.brush.decode_rle`).

## Versioning

Bump `--out-dir training/v2/` for a new dataset version. Never overwrite
`v1/` once shipped to ML team.
