---
description: Generate catalog YAML from instrument datasheet PDF. Extracts capabilities, channels, and specs into the structured capability schema with automated review.
argument-hint: "[pdf_path] [output_yaml]"
---

Read and follow `litmus/skills/workflow/datasheet-to-catalog.md` EXACTLY. It is a procedure with XML-tagged phases, steps, gates, and checkpoints. Execute every tag in order.

**Input:** `$ARGUMENTS` = `<pdf_path> <output_filename>`

**Claude Code adaptations:**
- Where the workflow says `refs/`, read source files directly: `docs/capability-schema.md` for schema; `src/litmus/models/enums.py` for units / MeasurementFunction / Comparator; `src/litmus/models/capability.py` for the Capability + SpecBand models
- Construct agent prompts inline using `src/litmus/skills/agents/*.md` as REFERENCE for what to include — do NOT read them at runtime and do NOT paste them verbatim. Build each prompt with the required inputs table from the workflow.
- Use Edit/Write tools to update YAML on disk (not MCP save)
- Use `uv run python -c "from pathlib import Path; from litmus.store import load_catalog_entry; load_catalog_entry(Path('<path>'))"` to validate
- Emit every `<phase-complete />` and `<gate-result />` tag the workflow requires
