---
name: catalog-scaffold
description: Quickly create a catalog entry from Claude's knowledge of common instruments. No datasheet needed for well-known models.
---

# Catalog Scaffold

<overview>
Create a basic catalog entry for a known instrument using Claude's existing knowledge.
This is a FAST path for common instruments - no PDF parsing, no multi-phase extraction.

Use this when:
- Discovered an instrument not in catalog
- Need to get started quickly
- Instrument is well-known (Keysight, Tektronix, Rigol, Fluke, etc.)

Use /catalog-from-datasheet instead when:
- Instrument is obscure or specialized
- You need exact accuracy specifications
- You need full condition matrices
</overview>

<inputs>
| Input | Required | Description |
|-------|----------|-------------|
| manufacturer | Yes | Instrument manufacturer (e.g., "Keysight") |
| model | Yes | Model number (e.g., "34461A") |
| project | Yes | Project root path |
</inputs>

<rules>
- Use your knowledge of the instrument - do NOT hallucinate specs you're unsure of
- If you don't know the instrument well, say so and suggest /catalog-from-datasheet
- Mark the entry with `scaffold: true` so users know to verify/refine
- Use conservative/approximate ranges - better to understate than overstate
- Include only capabilities you're confident about
</rules>

---

<phase id="1" name="Identify Instrument">

<step id="1.1">
Confirm you know this instrument. State:
- What type of instrument it is (DMM, PSU, oscilloscope, etc.)
- Key capabilities you know it has
- Confidence level (high/medium/low)

If confidence is LOW, recommend /catalog-from-datasheet instead and stop.
</step>

<step id="1.2">
Ask the user to confirm the instrument type if there's any ambiguity.
</step>

</phase>

---

<phase id="2" name="Generate Catalog Entry">

<step id="2.1">
Read the appropriate generic template for reference:
- DMM: src/litmus/catalog/generic/generic_dmm.yaml
- PSU: src/litmus/catalog/generic/generic_psu.yaml
- Oscilloscope: src/litmus/catalog/generic/generic_oscilloscope.yaml
- Electronic Load: src/litmus/catalog/generic/generic_eload.yaml
</step>

<step id="2.2">
Generate the catalog YAML with:

```yaml
id: {manufacturer}_{model}  # lowercase, underscores
manufacturer: {Manufacturer}
model: {Model}
name: {Manufacturer} {Model} {brief description}
description: {One-line description of the instrument}
type: {dmm|psu|oscilloscope|fgen|eload|smu|...}
scaffold: true  # IMPORTANT: marks this as needing verification

channels:
  # Based on your knowledge of this model

capabilities:
  # Only capabilities you're confident about
  # Use approximate ranges - round to common values
  # Include function, direction, signals with ranges
```

Key rules for scaffold entries:
- `scaffold: true` is REQUIRED - this flags it for later refinement
- Ranges should be CONSERVATIVE (understate rather than overstate)
- Only include capabilities you're confident about
- Channel count should match your knowledge of the model
</step>

<step id="2.3">
Show the draft to the user for approval before saving.
</step>

</phase>

---

<phase id="3" name="Save Entry">

<step id="3.1">
Save to: catalog/{manufacturer}/{manufacturer}_{model}.yaml

Create the manufacturer directory if it doesn't exist.
</step>

<step id="3.2">
Validate with:
```bash
uv run python -c "from pathlib import Path; from litmus.store import load_catalog_entry; load_catalog_entry(Path('catalog/{manufacturer}/{manufacturer}_{model}.yaml'))"
```
</step>

<step id="3.3">
Report success and remind user:
- Entry is marked `scaffold: true`
- Ranges are approximate
- Run /catalog-from-datasheet later for exact specs if needed
</step>

</phase>

---

<examples>

**Example 1: Well-known DMM**
```
User: scaffold Keysight 34461A

Claude: I know this instrument well (high confidence):
- 6.5 digit digital multimeter
- DC/AC voltage, DC/AC current, 2/4-wire resistance
- Frequency, period, capacitance, temperature
- Single channel

[Generates catalog entry with conservative ranges]
```

**Example 2: Unknown instrument**
```
User: scaffold Acme XYZ-9000

Claude: I don't have reliable information about the Acme XYZ-9000.
I recommend using /catalog-from-datasheet with the part datasheet
to ensure accurate specifications.
```

</examples>
