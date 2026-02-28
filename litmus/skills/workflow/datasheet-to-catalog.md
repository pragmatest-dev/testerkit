---
name: datasheet-to-catalog
description: Generate catalog YAML from an instrument datasheet PDF. Section-by-section extraction with parallel mechanical+semantic audit and writer-resume fix loop.
---

# Datasheet to Catalog YAML Workflow

<overview>
Generate a catalog YAML entry from an instrument datasheet PDF.

Architecture:
  Main Agent (orchestrator) — NEVER reads the PDF directly
    Phase 1: Get paths from user input
    Phase 2: Spawn section-splitter (opus) — reads PDF, outputs page ranges only
    Phase 3: Spawn scaffold-writer (opus) — reads targeted pages, writes device-level YAML
    Phase 4: For each section:
      extractor(opus) reads PDF, writes inventory file ONLY
      → writer(opus) reads inventory, writes YAML ONLY (no PDF)
      → reviewer-fixer(opus) reads inventory+YAML, finds issues, fixes them directly
      → audit script validates
      → loop until reviewer-fixer says "fixed nothing" AND audit passes, or max rounds
    Phase 5: Final verify
    Phase 6: Report

Key principles:
- PDF is read ONCE per section by the extractor agent — writer never touches the PDF
- Inventory file is the single source of truth for all downstream agents
- Reviewer-fixer reads inventory+YAML and makes surgical edits — no telephone game
- Orchestrator holds only file paths and report text, never PDF/inventory/YAML content
- Enum list is read ONCE from models.py and injected into agent prompts
</overview>

<rules>
- Execute every phase, every step, in order. No skipping. No reordering.
- The orchestrator NEVER reads the PDF. NEVER edits YAML values directly. It ONLY spawns agents and runs scripts.
- The orchestrator NEVER overrides agent outputs. Section maps, inventories, and audit/review reports are used AS RETURNED.
- Every agent prompt is constructed inline (do NOT read agent template files at runtime). Use the model specified in the agent's frontmatter — NEVER override it.
- Every audit+review result goes through the gate. The gate is arithmetic. No judgment.
- Paste findings VERBATIM into fix prompts. No filtering. No summarizing.
</rules>

---

<phase id="1" name="Get Paths + Enum List">

<step id="1.1">Get PDF path and output YAML path from user input.</step>

<step id="1.2">
Read `litmus/config/models.py` (lines 1-220).
Extract ALL enum values into these variables (read ONCE, injected into agent prompts):
- ENUM_FUNCTIONS — MeasurementFunction values
- ENUM_CONNECTORS — ConnectorType values
- ENUM_TERMINALS — TerminalRole values
- ENUM_GROUNDS — GroundTopology values
- ENUM_DIRECTIONS — Direction values
</step>

<checkpoint phase="1">
You MUST have PDF_PATH, YAML_PATH, INSTRUMENT_ID, and all ENUM_* variables.
Emit: <phase-complete id="1" />
</checkpoint>

</phase>

---

<phase id="2" name="Split">

<step id="2.1">
Construct the section-splitter prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| PDF_PATH | Full path to the datasheet PDF | User input |
| Instructions | Read PDF, build section map, identify scaffold pages, verify page coverage | See litmus/skills/agents/section-splitter.md for reference |

</step>

<step id="2.2">Spawn: Task(model="opus", prompt=constructed_prompt)</step>

<step id="2.3">
Parse the agent's return for:
- SECTION MAP — each entry becomes one extraction+writer cycle in Phase 4
- SCAFFOLD PAGES — overview, connector, and general spec page numbers for Phase 3
- PAGE COVERAGE — verify "All pages covered: YES". If NO, STOP and report the gaps.
- Skip reason — if the PDF is wrong/brochure
</step>

<step id="2.4">
If the agent reports SKIP:*, update QUEUE.md and STOP. Do NOT proceed to Phase 3.
</step>

<checkpoint phase="2">
You MUST have a numbered section map and scaffold page numbers before proceeding.
Emit: <phase-complete id="2" sections="N" />
</checkpoint>

</phase>

---

<phase id="3" name="Scaffold">

<step id="3.1">
Construct the scaffold-writer prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| PDF_PATH | Full path to the datasheet PDF | User input |
| YAML_PATH | Output YAML file path | User input |
| INSTRUMENT_ID | e.g., ni_pxie_4163 | User input |
| OVERVIEW_PAGES | Page range for overview/title | Phase 2 scaffold pages |
| CONNECTOR_PAGES | Page range for connectors/I/O | Phase 2 scaffold pages |
| GENERAL_PAGES | Page range for general/environmental specs | Phase 2 scaffold pages |
| ENUM_CONNECTORS | ConnectorType enum values | Phase 1 |
| ENUM_TERMINALS | TerminalRole enum values | Phase 1 |
| ENUM_GROUNDS | GroundTopology enum values | Phase 1 |
| Instructions | Read targeted pages, write device-level YAML, validate | See litmus/skills/agents/scaffold-writer.md for reference |
</step>

<step id="3.2">Spawn: Task(model="opus", prompt=constructed_prompt)</step>

<step id="3.3">
Parse the agent's return for:
- CHANNELS YAML — store as CHANNELS_YAML for writer prompts
- Status — verify scaffold validated clean
</step>

<checkpoint phase="3">
You MUST have CHANNELS_YAML and a validated scaffold before proceeding.
Emit: <phase-complete id="3" />
</checkpoint>

</phase>

---

<phase id="4" name="Section Loop">

For EACH non-skipped section in the section map, execute steps 4a → 4b → 4c → gate in order.
Do NOT process the next section until the current section's gate emits PASS or MAX_ROUNDS.

<step id="4a" name="Extract">
Construct the section-extractor prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| PDF_PATH | Full path to the datasheet PDF | Phase 1 |
| PAGES | Page range for this section | Section map |
| SECTION_NAME | Topic description | Section map |
| INVENTORY_PATH | `.tmp/inventory/<instrument_id>/inventory_section_N.md` | Generated path (temp dir, auto-cleaned) |
| Instructions | Read PDF, write inventory | See litmus/skills/agents/section-extractor.md for reference |

Spawn: Task(model="opus", prompt=constructed_prompt)
</step>

<step id="4b" name="Write">
Construct the section-writer prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| SECTION_NAME | Topic description | Section map |
| YAML_PATH | Output YAML file path | Phase 1 |
| CHANNELS_YAML | The channels dict from the scaffold | Phase 3 |
| ENUM_LIST | MeasurementFunction enum values | Phase 1 |
| INVENTORY_PATH | `.tmp/inventory/<instrument_id>/inventory_section_N.md` | Step 4a |
| Instructions | Read inventory, write YAML | See litmus/skills/agents/section-writer.md for reference |

Note: NO PDF_PATH — the writer reads the inventory, not the PDF.

Spawn: Task(model="opus", prompt=constructed_prompt)

After completion, verify the YAML loads:
```
uv run litmus validate --type catalog YAML_PATH
```
</step>

<step id="4c" name="Review-Fix + Audit loop">

This step loops until the reviewer-fixer reports "FIXED NOTHING" and audit passes, or max rounds.

**Each round:**

1. **Reviewer-fixer (opus agent):**
   Construct the reviewer-fixer prompt with:

   | Input | Description | Source |
   |-------|-------------|--------|
   | YAML_PATH | Output YAML file path | Phase 1 |
   | SECTION_NAME | Topic description | Section map |
   | INVENTORY_PATH | `.tmp/inventory/<instrument_id>/inventory_section_N.md` | Step 4a |
   | CAPABILITIES | Capability function names from this section | Step 4b return |
   | ENUM_LIST | MeasurementFunction enum values | Phase 1 |
   | Instructions | Review inventory vs YAML, fix issues directly | See litmus/skills/agents/section-reviewer.md for reference |

   Spawn: Task(model="opus", prompt=constructed_prompt)

   The reviewer-fixer reads inventory + YAML, runs 5 semantic checks, and directly edits the YAML to fix any issues it finds. It returns either "FIXED NOTHING" (all checks pass) or a report of what it fixed.

2. **Mechanical audit (instant):**
   ```
   uv run python scripts/audit_catalog.py YAML_PATH --capabilities <this section's capability functions>
   ```

3. **Gate:**
</step>

<gate id="section-gate">
EVERY time a review-fix + audit round completes, execute this gate.

Round counting: round 0 is the first review-fix after initial write. Each subsequent round increments R.

1. Check two conditions:
   - Did the reviewer-fixer report "FIXED NOTHING"?
   - Did the audit script report "PASS"?
2. Emit this tag:

   <gate-result section="N" round="R" findings="F" action="PASS|FAIL|MAX_ROUNDS" />

3. Decision:
   - Reviewer said "FIXED NOTHING" AND audit says PASS → action="PASS" → delete inventory file via `uv run python -c "from pathlib import Path; Path('INVENTORY_PATH').unlink()"`, proceed
   - R >= 3 → action="MAX_ROUNDS" → log unresolved issues, delete inventory file via `uv run python -c "from pathlib import Path; Path('INVENTORY_PATH').unlink()"`, proceed
   - otherwise → action="FAIL" → go back to step 4c for another round

NOTHING else exits the loop. No exceptions. No judgment calls.
</gate>

</phase>

---

<phase id="5" name="Final Verify">

After ALL sections are processed, format and validate:
```
uv run python -c "from litmus.config.fmt import format_file_inplace; format_file_inplace(Path('YAML_PATH'))"
uv run litmus validate --type catalog YAML_PATH
```

Also run the full audit (no --capabilities scope):
```
uv run python scripts/audit_catalog.py YAML_PATH
```

Fix any remaining errors.

<checkpoint phase="5">
Emit: <phase-complete id="5" capabilities="N" />
</checkpoint>

</phase>

---

<phase id="6" name="Report">

Report final stats:
- Total capabilities
- Signals with resolution
- SpecBands count
- Controls count
- Conditions count
- Attributes count (with specs vs without)
- Per-section: round count, final findings

<checkpoint phase="6">
Emit: <phase-complete id="6" />
</checkpoint>

</phase>
