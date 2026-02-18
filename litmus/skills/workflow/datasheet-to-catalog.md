---
name: datasheet-to-catalog
description: Generate catalog YAML from an instrument datasheet PDF. Section-by-section extraction (sonnet) with per-section audit (opus) and orchestrator-driven fixes.
---

# Datasheet to Catalog YAML Workflow

Generate a catalog YAML entry from an instrument datasheet PDF. Each PDF section gets extracted by a sonnet agent, then immediately audited by an opus agent. The orchestrator fixes audit findings before moving to the next section.

## Architecture

```
Main Agent (orchestrator)
  ├── Phase 1: Load schema + enum refs as text
  ├── Phase 2: Skim PDF, build section map
  ├── Phase 3: Write scaffold YAML (header, channels, empty capabilities)
  ├── Phase 4: For each section:
  │     ├── 4a: Spawn section-processor (sonnet) — extract
  │     ├── 4b: Spawn catalog-reviewer (opus) — audit that section only
  │     └── 4c: Fix audit findings
  ├── Phase 5: Final verify via load_catalog_entry()
  └── Phase 6: Report final stats
```

## MCP Tools Available

| Tool | Purpose |
|------|---------|
| `litmus(action="read", path="...")` | Read reference docs and existing files |
| `litmus(action="save", type="catalog", id="...", content={...}, project=...)` | Save catalog YAML |
| `litmus(action="enum_reference")` | Full MeasurementFunction enum table |
| `litmus(action="lookup_enum", id="...")` | Resolve abbreviation to enum value |

**IMPORTANT:** Pass `project=<project_root>` to ALL calls after init.

---

## Phase 1: Load Context

1. Get PDF path and output filename from user
2. Read the schema reference and store as text variable `SCHEMA_REF`:
   - **MCP:** `litmus(action="read", path="refs/capability-schema.md")`
   - **Claude Code:** Read `docs/capability-schema.md`
3. Read the enum reference and store as text variable `ENUM_REF`:
   - **MCP:** `litmus(action="read", path="refs/enums.md")`
   - **Claude Code:** Read `litmus/config/models.py` (lines 1-580 for enums)
4. These two text blobs will be injected into every subagent prompt

---

## Phase 2: Build Section Map

Skim the entire PDF to identify section boundaries. Read page 1 (often has TOC), then skim headers on each page (read 4-6 pages at a time, focusing only on headings — don't extract specs yet).

Build a section map:
```
1. Introduction / Overview — pages 1-4 (skip, no specs)
2. Analog Input — pages 10-14
3. Analog Output — pages 14-15
...
```

Each entry becomes one extract+audit cycle in Phase 4. Mark sections without specs as "skip". Print the section map before proceeding.

---

## Phase 3: Write Scaffold to Disk

1. Using what you learned from the skim, **write the initial YAML NOW**: header comment (3 lines max), `catalog_entry`, `channels` dict, `capabilities: []`
2. Include ALL channels — front panel AND rear panel connectors. Every physical connector with documented electrical specs is a channel.
3. Use compact range syntax: `"ai[0:7]"` not arrays of individual names
4. The file MUST exist on disk before moving to Phase 4
5. Extract the channels YAML text and store as `CHANNELS_YAML` for subagent prompts

---

## Phase 4: Section Loop — Extract, Audit, Fix

For each non-skipped section, run three steps before moving to the next section:

### Step 4a: Extract — Spawn section-processor (sonnet)

1. Read the agent template:
   - **Claude Code:** Read `litmus/skills/agents/section-processor.md`
   - **MCP:** `litmus(action="read", path="agents/section-processor.md")`

2. Replace all `{{variables}}` in the template:
   - `{{PDF_PATH}}` → the PDF file path
   - `{{PAGES}}` → e.g., "10-14"
   - `{{SECTION_NAME}}` → e.g., "Analog Input"
   - `{{YAML_PATH}}` → the output YAML file path
   - `{{CHANNELS_YAML}}` → the channels dict from Phase 3
   - `{{SCHEMA_REF}}` → the full schema text from Phase 1
   - `{{ENUM_REF}}` → the full enum text from Phase 1

3. Spawn: `Task(model="sonnet", prompt=<populated template>)`

4. Wait for completion. Verify the YAML loads:
   ```python
   python -c "from litmus.catalog.loader import load_catalog_entry; load_catalog_entry('<yaml_path>')"
   ```

### Step 4b: Audit — Spawn catalog-reviewer (opus)

The reviewer audits **only this section's pages**, not the whole PDF.

1. Read the agent template:
   - **Claude Code:** Read `litmus/skills/agents/catalog-reviewer.md`
   - **MCP:** `litmus(action="read", path="agents/catalog-reviewer.md")`

2. Replace all `{{variables}}`:
   - `{{PDF_PATH}}` → the PDF file path
   - `{{YAML_PATH}}` → the output YAML file path
   - `{{SECTION_MAP}}` → **only the current section** (e.g., "Analog Input — pages 10-14")
   - `{{SCHEMA_REF}}` → the full schema text from Phase 1
   - `{{ENUM_REF}}` → the full enum text from Phase 1

3. Spawn: `Task(model="opus", prompt=<populated template>)`

4. Wait for completion. The reviewer returns a structured audit report.

### Step 4c: Fix Findings

Parse the reviewer's audit report. For each finding:
1. Apply the fix to the YAML (Edit tool)
2. Run `load_catalog_entry()` after all fixes to verify

Then proceed to the next section.

---

## Phase 5: Final Verify

After all sections are processed, run a final load check:
```python
python -c "from litmus.catalog.loader import load_catalog_entry; e = load_catalog_entry('<yaml_path>'); print(f'{len(e.capabilities)} capabilities')"
```

Fix any remaining errors.

---

## Phase 6: Report

Report final stats:
- Total capabilities
- Signals with resolution
- SpecBands count
- Controls count
- Conditions count
- Attributes count
- Per-section audit scores (X/8 checks passing, completeness %)
