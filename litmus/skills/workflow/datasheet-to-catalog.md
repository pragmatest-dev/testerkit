---
name: datasheet-to-catalog
description: Generate catalog YAML from an instrument datasheet PDF. Section-by-section extraction (sonnet) with per-section audit (opus) and orchestrator-driven fixes.
---

# Datasheet to Catalog YAML Workflow

Generate a catalog YAML entry from an instrument datasheet PDF. Each PDF section gets extracted by a sonnet agent, then immediately audited by an opus agent. The orchestrator fixes audit findings before moving to the next section.

## Architecture

```
Main Agent (orchestrator) — NEVER reads the PDF directly
  ├── Phase 1: Load schema + enum refs as text
  ├── Phase 2+3: Spawn section-mapper (sonnet) — skims PDF, builds section map, writes scaffold YAML
  ├── Phase 4: For each section:
  │     ├── 4a: Spawn section-processor (sonnet) — extract
  │     ├── 4b: Spawn catalog-reviewer (opus) — audit that section only
  │     └── 4c: Fix audit findings (orchestrator reads YAML + audit report only, never the PDF)
  ├── Phase 5: Final verify via load_catalog_entry()
  └── Phase 6: Report final stats
```

**Key principle:** The orchestrator's context window never contains PDF content. All PDF reading happens inside subagents. The orchestrator only sees: section maps, YAML files, audit reports, and validation output.

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

## Phase 2+3: Section Map + Scaffold (section-mapper agent)

Spawn a **section-mapper agent** to skim the PDF, build the section map, and write the scaffold YAML. This keeps the PDF out of the orchestrator's context.

1. Construct the section-mapper prompt directly (do NOT read the agent template file). The prompt MUST contain:

   | Input | Description | Source |
   |-------|-------------|--------|
   | `PDF_PATH` | Full path to the datasheet PDF | User input |
   | `YAML_PATH` | Output YAML file path (e.g., `catalog/ni/ni_pxie_4163.yaml`) | User input |
   | `INSTRUMENT_ID` | e.g., `ni_pxie_4163` | User input |
   | `SCHEMA_REF` | Full capability schema text — channel topology, signal/condition/control/attribute definitions | `docs/capability-schema.md` |
   | `ENUM_REF` | Full enum text — MeasurementFunction, ConnectorType, TerminalRole, GroundTopology | `litmus/config/models.py` lines 1-580 |
   | Instructions | Skim PDF, build section map (with critical rules), write scaffold YAML, validate | See `litmus/skills/agents/section-mapper.md` for reference |

2. Spawn: `Task(model="sonnet", prompt=<constructed prompt>)`

4. Parse the agent's return for:
   - **SECTION MAP** — each entry becomes one extract+audit cycle in Phase 4
   - **CHANNELS YAML** — store as `CHANNELS_YAML` for section-processor prompts
   - **Skip reason** — if the PDF is wrong/brochure, mark in QUEUE.md and stop

5. If the agent reports `SKIP:*`, update QUEUE.md and move to the next entry. Do NOT proceed to Phase 4.

---

## Phase 4: Section Loop — Extract, Audit, Fix

For each non-skipped section, run three steps before moving to the next section:

### Step 4a: Extract — Spawn section-processor (opus)

1. Construct the section-processor prompt directly (do NOT read the agent template file). The prompt MUST contain:

   | Input | Description | Source |
   |-------|-------------|--------|
   | `PDF_PATH` | Full path to the datasheet PDF | Same as Phase 1 |
   | `PAGES` | Page range for this section (e.g., `3-5`) | Section map |
   | `SECTION_NAME` | Topic description (e.g., "DC Voltage Output") | Section map |
   | `YAML_PATH` | Output YAML file path | Same as Phase 1 |
   | `CHANNELS_YAML` | The channels dict from the scaffold | Section mapper output |
   | `SCHEMA_REF` | Full capability schema text | Phase 1 |
   | `ENUM_REF` | Full enum text — so the agent uses valid MeasurementFunction values | Phase 1 |
   | Instructions | Extraction rules, parameter placement guide, scope rule, common mistakes | See `litmus/skills/agents/section-processor.md` for reference |
   | Audit findings | **Only in fix mode (step 4c):** paste the ENTIRE audit report verbatim | Previous audit output |

2. Spawn: `Task(model="opus", prompt=<constructed prompt>)`

4. Wait for completion. Verify the YAML loads:
   ```python
   python -c "from litmus.catalog.loader import load_catalog_entry; load_catalog_entry('<yaml_path>')"
   ```

### Step 4b: Audit — Spawn catalog-reviewer (opus)

The reviewer audits **only this section's pages**, not the whole PDF.

1. Construct the catalog-reviewer prompt directly (do NOT read the agent template file). The prompt MUST contain:

   | Input | Description | Source |
   |-------|-------------|--------|
   | `PDF_PATH` | Full path to the datasheet PDF | Same as Phase 1 |
   | `YAML_PATH` | Output YAML file path | Same as Phase 1 |
   | `SECTION_MAP` | **Only the current section** (e.g., "DC Voltage Output — pages 3-5") | Section map |
   | `SCHEMA_REF` | Full capability schema text — so the auditor knows valid field placements | Phase 1 |
   | `ENUM_REF` | Full enum text — so the auditor knows which MeasurementFunction values are VALID. **Without this, the auditor will guess and produce false positives on enum checks.** | Phase 1 |
   | Instructions | The 8 audit checks and return format | See `litmus/skills/agents/catalog-reviewer.md` for reference |

2. Spawn: `Task(model="opus", prompt=<constructed prompt>)`

4. Wait for completion. The reviewer returns a structured audit report.

### Step 4c: Fix Findings — LOOP until clean

The orchestrator NEVER reads the PDF and NEVER edits YAML values directly. It only spawns agents.

**If the audit has ANY findings (MISSING, MISPLACED, WRONG_VALUE, or WRONG_ENUM):**

1. Spawn a new **section-processor agent** with the same PDF/pages/section/YAML as step 4a, PLUS the **ENTIRE audit report pasted verbatim** into the prompt. Do NOT filter, prioritize, summarize, editorialize on severity, add skip instructions, or omit any findings — paste the full audit output exactly as returned. The orchestrator does not judge which findings matter; the fix agent reads the PDF and decides.
2. The agent re-reads the PDF pages, reads the YAML, applies fixes, and validates.
3. After the fix agent completes, go back to **step 4b** — spawn a new catalog-reviewer to re-audit this section.
4. Repeat until the audit comes back with all 8 checks passing.

**If the audit is clean (8/8 passing):** proceed to the next section.

**Max iterations:** 3 rounds per section. If still failing after 3 rounds, note the remaining findings and move on.

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
