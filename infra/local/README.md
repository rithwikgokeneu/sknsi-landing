# Phase 1 — Local Label Studio (Docker)

Spin up Label Studio + Postgres locally to validate the annotation workflow before deploying to VPS.

## Prereqs

- Docker Desktop running
- ~2 GB free disk

## Setup

```bash
cd infra/local
cp .env.example .env
# edit .env — set strong POSTGRES_PASSWORD and LABEL_STUDIO_PASSWORD
docker compose up -d
```

Wait ~30s for Postgres healthcheck + Label Studio boot, then open:

http://localhost:8080

Login with the admin credentials from `.env`.

## Create the project

1. **Create Project** → name: `SKNSI Pilot — Batch 001 Round 1`
2. **Labeling Setup** → paste contents of `../labeling/template.xml` (Maher Schema v1.0)
3. **Data Import** → upload `../labeling/sample_tasks.json` (Wasabi URLs — needs Phase 3 keys) **or** drag 2 local test images for pure Phase 1 isolation
4. Annotate 2–3 tasks end-to-end to verify all required fields work
5. **Export** → JSON → run the verification checklist below against the exported file

## Verify before moving to Phase 2

Schema reference: `infra/labeling/template.xml` — Maher Schema v1.0 (sections 0, A–F).

### Drawing / region behavior
- [ ] `lesion_brush` (BrushLabels) — all 7 colors paint and save
- [ ] `lesion_boundary` (PolygonLabels) — polygon points save with normalized coords 0–100
- [ ] Per-lesion (`perRegion="true"`) attributes appear only when a polygon is selected

### Required fields (must block submit when empty)
- [ ] Section 0: `annotation_status`
- [ ] Section A: `image_modality`, `lighting`, `image_quality`, `scale_reference`, `framing`
- [ ] Section B: `anatomic_region`, `laterality`, `contralateral_visible`, `patient_position`
- [ ] Section C: `pen_marks`
- [ ] Section D: `diagnosis`, `confirmation_status`, `fitzpatrick`
- [ ] Section F: `diagnosis_confidence`, `segmentation_confidence`, `needs_second_opinion`

### Conditional visibility
- [ ] `skip_reason` shows only when `annotation_status` ≠ "Annotate (proceed)"
- [ ] `image_quality_reason` shows only when `image_quality` ∈ {Borderline, Unusable}
- [ ] `pen_colors` + `written_annotations` show only when `pen_marks` ≠ None
- [ ] All Section E + drawing tools hidden unless `annotation_status` = "Annotate (proceed)"

### Export JSON must contain (per task)
- [ ] Regions: `lesion_boundary` polygon(s) with `points`, `lesion_brush` brush mask(s) with `rle`
- [ ] Image-level fields: `annotation_status`, `image_modality`, `lighting`, `image_quality`, `image_quality_reason` (if shown), `scale_reference`, `framing`, `anatomic_region`, `laterality`, `contralateral_visible`, `patient_position`, `pen_marks`, `pen_colors` (if shown), `written_annotations` (if shown), `surface_artifacts`, `preexisting_features`, `privacy_issues`, `diagnosis`, `confirmation_status`, `fitzpatrick`, `coexisting_findings`, `diagnosis_confidence`, `segmentation_confidence`, `needs_second_opinion`, `notes`
- [ ] Per-region fields on each `lesion_boundary` polygon: `interior_exclusions`, `border_definition`, `lesion_extent`, `erythema_color`, `erythema_uniformity`, `hemosiderin`, `yellow_tones`, `surface_texture`, `edema`, `lesion_features`, `red_flag`
- [ ] LS-auto fields present: annotator id, created_at, updated_at, lead_time

### Access control
- [ ] `LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true` blocks signup without invite link (try `/user/signup/` anonymously → should fail)

### Sign-off artifact
- [ ] Save a sample export from 2 annotated tasks to `infra/labeling/sample_export.json` as proof Phase 1 passed

## Stop / reset

```bash
docker compose down            # keep data
docker compose down -v         # wipe Postgres + LS data (destructive)
```

## Notes

- Anonymous signup disabled. Generate invite links from **Organization** → **People** in the LS UI.
- Local volumes persist annotations across restarts.
- Wasabi-hosted images require either presigned URLs or LS source storage configured (see Phase 3).
