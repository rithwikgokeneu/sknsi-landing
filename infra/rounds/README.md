# Phase 4 — Multi-round annotation orchestration

Manages the Round 1 → Round 2 → (optional Round 3 escalation) annotation
workflow on top of the prod Label Studio instance (Phase 2) and the
Wasabi-hosted batches (Phase 3). Outputs feed Phase 5 (`scripts/export_pipeline/`).

## Workflow

```
                                ┌────────────────────────────────────┐
Batch uploaded                  │                                    │
to Wasabi (Phase 3)             │                                    │
       │                        ▼                                    │
       ▼                Round 3 review project                       │
  Round 1 project        (only escalated images)                     │
  primary annotator             │                                    │
       │                        │                                    │
       ▼                        │                                    │
  export_round.py               │                                    │
       │                        │                                    │
       ▼                        │                                    │
  Round 2 project ──▶ agreement.py (Phase 5) ──▶ disagreements.csv ──┘
  independent reviewer          │
       │                        ▼
       ▼               consensus.csv (Phase 5)
  export_round.py
```

## Prereqs

- Phase 2 stack running at `https://sknsi.com/annotate`
- Phase 3 batch uploaded; LS import JSON in hand (output of
  `infra/wasabi/upload_batch.py`)
- LS admin API token: log in to LS → **Account & Settings → Access Token**
- Annotator accounts created via invite link (LS UI → **Organization → People**)
- Python deps: `pip install label-studio-sdk boto3`

## Environment

```bash
export LS_URL="https://sknsi.com/annotate"
export LS_TOKEN="<admin API token>"

# For pushing exports to Wasabi
export WASABI_ACCESS_KEY=...
export WASABI_SECRET_KEY=...
export WASABI_BUCKET=sknsi-annotation
export WASABI_REGION=us-east-1
```

## Run a round

```bash
# 1. Create Round 1 project, attach Maher schema, import batch tasks
python create_round_project.py \
  --batch-id batch_001 --round 1 \
  --template ../labeling/template.xml \
  --tasks ../labeling/batch_001_round_1.json \
  --assignees alice@hospital.org bob@hospital.org
# → prints project_id, save it

# 2. Annotators log in to https://sknsi.com/annotate and label until 100%.
#    Monitor:
python monitor_progress.py --project-id 12

# 3. Export the round, archive raw JSON to Wasabi exports/, write local copy
python export_round.py \
  --project-id 12 --batch-id batch_001 --round 1 \
  --out exports/batch_001/round_1_export.json

# 4. Repeat steps 1-3 for Round 2 (different annotators).
#    Use the SAME tasks JSON so reviewers see the same images.

# 5. Run Phase 5 parse_export.py + agreement.py.
#    If disagreements.csv is non-empty, escalate:
python escalate_disagreements.py \
  --batch-id batch_001 \
  --disagreements ../../scripts/export_pipeline/disagreements/batch_001_disagreements.csv \
  --tasks-source ../labeling/batch_001_round_1.json \
  --out ../labeling/batch_001_round_3.json \
  --template ../labeling/template.xml \
  --assignees dermatologist@hospital.org

# 6. Round 3 annotator labels, then export_round.py + re-run Phase 5
#    including the round_3 CSV.
```

## Naming convention

- LS project: `SKNSI Pilot — <batch_id> Round <N>`
- LS user role: `annotator` (round 1+2), `reviewer` (round 3 dermatologist)
- Export JSON: `exports/<batch_id>/round_<N>_export.json`
- Wasabi key: `exports/<batch_id>/round_<N>_export.json` (mirrors local path)

## Quality gates per round

Before exporting, confirm in the LS UI:

- [ ] 100% tasks completed (no skipped / in-progress)
- [ ] All annotators have submitted, not just saved drafts
- [ ] Spot-check 3-5 random tasks: required fields filled, polygon + brush regions plausible
- [ ] `annotation_status` distribution matches expectation (most should be "Annotate (proceed)")

## Security

- API token has admin scope — store in `/opt/sknsi-annotate/.env` (chmod 600),
  never commit, never paste into Slack
- Rotate quarterly or after any annotator departure
- Annotator invite links expire 7 days by default; regenerate as needed
