---
description: Generate catalog YAML from instrument datasheet PDF
---

Read and follow the workflow file at `litmus/skills/workflow/datasheet-to-catalog.md` EXACTLY. It is a procedure with XML-tagged phases, steps, gates, and checkpoints. Execute every tag in order.

**Input:** The user will provide `<pdf_path>` and `<output_filename>`.

**Key references:**
- Schema: `docs/capability-schema.md`
- Capability + SpecBand: `src/litmus/models/capability.py`
- Enums (units, MeasurementFunction, Comparator): `src/litmus/models/enums.py`
- Agent specs: `src/litmus/skills/agents/*.md` (use as reference for prompt construction)

**Validation:** Run `uv run python -c "from pathlib import Path; from litmus.store import load_catalog_entry; load_catalog_entry(Path('<path>'))"` to validate output.
