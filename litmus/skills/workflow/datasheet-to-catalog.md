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
    Phase 2+3: Spawn section-mapper (sonnet) — skims PDF, builds section map, writes scaffold YAML
    Phase 4: For each section:
      writer(opus) reads PDF, writes inventory file, writes YAML
      → reviewer-fixer(opus) reads inventory+YAML, finds issues, fixes them directly
      → audit script validates
      → loop until reviewer-fixer says "fixed nothing" AND audit passes, or max rounds
    Phase 5: Final verify
    Phase 6: Report

Key principles:
- PDF is read ONCE per section by the writer agent
- Inventory file compresses PDF into compact text for the reviewer-fixer
- Reviewer-fixer reads inventory+YAML and makes surgical edits — no telephone game
- Orchestrator holds only file paths and report text, never PDF/inventory/YAML content
- Enum list is read ONCE from models.py and injected into agent prompts
</overview>

<rules>
- Execute every phase, every step, in order. No skipping. No reordering.
- The orchestrator NEVER reads the PDF. NEVER edits YAML values directly. It ONLY spawns agents and runs scripts.
- The orchestrator NEVER overrides agent outputs. Section maps, inventories, and audit/review reports are used AS RETURNED.
- Every agent prompt is constructed inline (do NOT read agent template files at runtime).
- Every audit+review result goes through the gate. The gate is arithmetic. No judgment.
- Paste findings VERBATIM into fix prompts. No filtering. No summarizing.
</rules>

---

<phase id="1" name="Get Paths + Enum List">

<step id="1.1">Get PDF path and output YAML path from user input.</step>

<step id="1.2">
Read the MeasurementFunction enum from `litmus/config/models.py` (lines 1-215).
Extract ALL enum values into ENUM_LIST. This is read ONCE and injected into all agent prompts.
</step>

<checkpoint phase="1">
You MUST have PDF_PATH, YAML_PATH, INSTRUMENT_ID, and ENUM_LIST.
Emit: <phase-complete id="1" />
</checkpoint>

</phase>

---

<phase id="2+3" name="Section Map + Scaffold">

<step id="2.1">
Construct the section-mapper prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| PDF_PATH | Full path to the datasheet PDF | User input |
| YAML_PATH | Output YAML file path | User input |
| INSTRUMENT_ID | e.g., ni_pxie_4163 | User input |
| Instructions | Skim PDF, build section map, write scaffold YAML, validate | See litmus/skills/agents/section-mapper.md for reference |

The agent reads its own schema/enum references from source files.
</step>

<step id="2.2">Spawn: Task(model="sonnet", prompt=constructed_prompt)</step>

<step id="2.3">
Parse the agent's return for:
- SECTION MAP — each entry becomes one writer cycle in Phase 4
- CHANNELS YAML — store as CHANNELS_YAML for writer prompts
- Skip reason — if the PDF is wrong/brochure
</step>

<step id="2.4">
If the agent reports SKIP:*, update QUEUE.md and STOP. Do NOT proceed to Phase 4.
</step>

<checkpoint phase="2+3">
You MUST have a numbered section map and CHANNELS_YAML before proceeding.
Emit: <phase-complete id="2+3" sections="N" />
</checkpoint>

</phase>

---

<phase id="4" name="Section Loop">

For EACH non-skipped section in the section map, execute steps 4a → 4b → gate in order.
Do NOT process the next section until the current section's gate emits PASS or MAX_ROUNDS.

<step id="4a" name="Write">
Construct the section-writer prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| PDF_PATH | Full path to the datasheet PDF | Phase 1 |
| PAGES | Page range for this section | Section map |
| SECTION_NAME | Topic description | Section map |
| YAML_PATH | Output YAML file path | Phase 1 |
| CHANNELS_YAML | The channels dict from the scaffold | Phase 2+3 |
| ENUM_LIST | MeasurementFunction enum values | Phase 1 |
| INVENTORY_PATH | `.claude/tmp/inventory/<instrument_id>/inventory_section_N.md` | Generated path (temp dir, auto-cleaned) |
| Instructions | Read PDF, write inventory, write YAML | See litmus/skills/agents/section-writer.md for reference |

Spawn: Task(model="opus", prompt=constructed_prompt)

After completion, verify the YAML loads:
```
uv run litmus validate YAML_PATH
```
</step>

<step id="4b" name="Review-Fix + Audit loop">

This step loops until the reviewer-fixer reports "FIXED NOTHING" and audit passes, or max rounds.

**Each round:**

1. **Reviewer-fixer (opus agent):**
   Construct the reviewer-fixer prompt with:

   | Input | Description | Source |
   |-------|-------------|--------|
   | YAML_PATH | Output YAML file path | Phase 1 |
   | SECTION_NAME | Topic description | Section map |
   | INVENTORY_PATH | `.claude/tmp/inventory/<instrument_id>/inventory_section_N.md` | Step 4a |
   | CAPABILITIES | Capability function names from this section | Step 4a return |
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
   - Reviewer said "FIXED NOTHING" AND audit says PASS → action="PASS" → delete inventory file, proceed
   - R >= 3 → action="MAX_ROUNDS" → log unresolved issues, delete inventory file, proceed
   - otherwise → action="FAIL" → go back to step 4b for another round

NOTHING else exits the loop. No exceptions. No judgment calls.
</gate>

</phase>

---

<phase id="5" name="Final Verify">

After ALL sections are processed, run a final validation:
```
uv run litmus validate YAML_PATH
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
