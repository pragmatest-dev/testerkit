---
description: Process ALL pending entries in catalog/QUEUE.md using the /catalog-from-datasheet skill for each one.
---

# Process Catalog Queue

Autonomously process every pending entry in the catalog queue.

<rules>
- ALWAYS use the /catalog-from-datasheet skill via the Skill tool — NEVER manually write catalog YAML
- Do NOT stop between instruments — the system auto-compresses context as needed
- Never ask the user to compact or do anything manually. This runs fully autonomously.
- If a PDF is a marketing brochure with no spec tables, mark as skip:brochure in QUEUE.md and continue
</rules>

<loop>

<step id="1">Read catalog/QUEUE.md</step>

<step id="2">Find the first entry with status pending or pending:redo</step>

<step id="3">If none found → print summary of all done entries and STOP</step>

<step id="4">Extract the id and pdf columns from that row</step>

<step id="5">
If pending:redo → delete the existing YAML at catalog/{path matching id}.yaml so the skill starts fresh.
</step>

<step id="6">
Run the skill: Use the Skill tool to invoke catalog-from-datasheet with args:
agent-os/specs/2026-02-06-catalog-master-list/research/pdfs/{pdf_column} catalog/{vendor}/{id_column}.yaml
</step>

<step id="7">
After the skill completes and YAML is validated, update QUEUE.md:
change that entry's status to done with final stats (capabilities, SpecBands, etc.)
</step>

<step id="8">Go back to step 1 — process the next entry.</step>

</loop>
