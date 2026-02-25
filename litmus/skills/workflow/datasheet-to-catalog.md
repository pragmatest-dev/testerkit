---
name: datasheet-to-catalog
description: Generate catalog YAML from an instrument datasheet PDF. Section-by-section extraction with per-section audit and orchestrator-driven fixes.
---

# Datasheet to Catalog YAML Workflow

<overview>
Generate a catalog YAML entry from an instrument datasheet PDF.

Architecture:
  Main Agent (orchestrator) — NEVER reads the PDF directly
    Phase 1: Get paths from user input
    Phase 2+3: Spawn section-mapper (sonnet) — skims PDF, builds section map, writes scaffold YAML
    Phase 4: For each section: inventory → extract → audit → gate → fix loop
    Phase 5: Final verify via litmus validate
    Phase 6: Report final stats

Key principle: The PDF is read ONCE per section by the inventory agent.
The inventory is then reused by the processor, auditor, and fix agents —
no agent re-reads the PDF. The orchestrator never sees PDF content.

All agents read their own reference files (schema, enums, examples) directly —
the orchestrator does NOT load or inject these.
</overview>

<rules>
- Execute every phase, every step, in order. No skipping. No reordering.
- The orchestrator NEVER reads the PDF. NEVER edits YAML values directly. It ONLY spawns agents.
- The orchestrator NEVER overrides agent outputs. Section maps, inventories, and audit reports are used AS RETURNED. The orchestrator has no basis to second-guess agents that read the PDF — it didn't read the PDF.
- Every agent prompt is constructed inline (do NOT read agent template files at runtime).
- Every audit result goes through the gate. The gate is arithmetic. No judgment.
- Paste audit reports VERBATIM into fix agents. No filtering. No summarizing.
</rules>

---

<phase id="1" name="Get Paths">

<step id="1.1">Get PDF path and output YAML path from user input.</step>

<checkpoint phase="1">
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
- SECTION MAP — each entry becomes one extract+audit cycle in Phase 4
- CHANNELS YAML — store as CHANNELS_YAML for section-processor prompts
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

For EACH non-skipped section in the section map, execute steps 4a → 4b → 4c → gate in order.
Do NOT process the next section until the current section's gate emits PASS or MAX_ROUNDS.

<step id="4a" name="Inventory">
Construct the section-inventory prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| PDF_PATH | Full path to the datasheet PDF | Phase 1 |
| PAGES | Page range for this section | Section map |
| SECTION_NAME | Topic description | Section map |
| Instructions | Read PDF, list every table/row/footnote | See litmus/skills/agents/section-inventory.md for reference |

Spawn: Task(model="opus", prompt=constructed_prompt)

Store the returned inventory as SECTION_INVENTORY. This inventory is reused by ALL subsequent agents for this section — processor, auditor, and fix agents. The PDF is NOT read again.
</step>

<step id="4b" name="Extract">
Construct the section-processor prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| SECTION_NAME | Topic description | Section map |
| YAML_PATH | Output YAML file path | Phase 1 |
| CHANNELS_YAML | The channels dict from the scaffold | Phase 2+3 |
| INVENTORY | The full inventory from step 4a | Step 4a |
| Instructions | Construction steps, cross-check | See litmus/skills/agents/section-processor.md for reference |
| Audit findings | ONLY in fix mode: paste the ENTIRE audit report verbatim | Previous audit |

Note: The processor does NOT receive PDF_PATH. It works entirely from the inventory.
The processor reads its own schema/enum/examples references from source files.

Spawn: Task(model="opus", prompt=constructed_prompt)

After completion, verify the YAML loads:
```
uv run litmus validate YAML_PATH
```
</step>

<step id="4c" name="Audit">
Construct the catalog-reviewer prompt. The prompt MUST contain ALL of these inputs:

| Input | Description | Source |
|-------|-------------|--------|
| YAML_PATH | Output YAML file path | Phase 1 |
| SECTION_NAME | Topic description | Section map |
| INVENTORY | The full inventory from step 4a | Step 4a |
| Instructions | The 8 audit checks and return format | See litmus/skills/agents/catalog-reviewer.md for reference |

Note: The auditor does NOT receive PDF_PATH. It checks YAML against the inventory.
The auditor reads its own schema/enum references from source files.

Spawn: Task(model="sonnet", prompt=constructed_prompt)

Wait for completion. The reviewer returns a structured audit report with "Overall: X/8 checks passing".
</step>

<gate id="section-gate">
EVERY time an audit report returns, execute this gate BEFORE doing anything else.

A "round" is one COMPLETE fix→audit cycle. Round counting:
- The first audit (after initial extract) is round 0 — it does NOT count toward the max.
- Each subsequent fix→audit cycle increments R: fix1→audit1 = round 1, fix2→audit2 = round 2, etc.
- This guarantees every audit's findings get at least one fix attempt.

1. Parse the "Overall: X/8 checks passing" line from the audit report.
2. You MUST emit this tag:

   <gate-result section="N" round="R" score="X" max="8" action="PASS|FAIL|MAX_ROUNDS" />

3. Decision — ARITHMETIC ONLY, do NOT evaluate finding severity:
   - X == 8 → action="PASS" → proceed to next section
   - R >= 3 → action="MAX_ROUNDS" → log unresolved findings, proceed to next section
   - otherwise → action="FAIL" → execute on-gate-fail below

NOTHING else exits the loop. No exceptions. No judgment calls.
No "these findings are minor." No "close enough." The number is the number.
</gate>

<on-gate-fail>
1. Spawn a NEW section-processor agent (step 4b) with the same inventory/section/YAML,
   PLUS the ENTIRE audit report pasted VERBATIM into the prompt.
   The processor works from the SAME inventory — no PDF re-read needed.
   Do NOT filter, prioritize, summarize, editorialize on severity, add skip instructions,
   or omit any findings. Paste the full audit output exactly as returned.
   The orchestrator does not judge which findings matter.
2. After the fix agent completes and YAML validates, go to step 4c (new audit).
3. After the new audit returns, go to the gate above.
</on-gate-fail>

</phase>

---

<phase id="5" name="Final Verify">

After ALL sections are processed, run a final validation:
```
uv run litmus validate YAML_PATH
```

Fix any remaining load errors.

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
- Attributes count
- Per-section audit scores (X/8 checks passing, completeness %)

<checkpoint phase="6">
Emit: <phase-complete id="6" />
</checkpoint>

</phase>
