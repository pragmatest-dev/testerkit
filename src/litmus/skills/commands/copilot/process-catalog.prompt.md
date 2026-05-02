---
description: Process ALL pending entries in catalog/QUEUE.md using the catalog-from-datasheet workflow for each one
---

# Process Catalog Queue

Autonomously process every pending entry in the catalog queue.

## Rules
- ALWAYS follow `litmus/skills/workflow/datasheet-to-catalog.md` for each entry — NEVER manually write catalog YAML
- Do NOT stop between instruments — process all pending entries in sequence
- If a PDF is a marketing brochure with no spec tables, mark as `skip:brochure` in QUEUE.md and continue

## Steps

1. Read `catalog/QUEUE.md`
2. Find the first entry with status `pending` or `pending:redo`
3. If none found, print summary of all done entries and STOP
4. Extract the `id` and `pdf` columns from that row
5. If `pending:redo`, delete the existing YAML at `catalog/{path matching id}.yaml`
6. Follow the `litmus/skills/workflow/datasheet-to-catalog.md` workflow with the PDF path and output YAML path
7. After YAML is validated, update QUEUE.md status to `done` with final stats
8. Go back to step 1
