---
name: section-writer
description: Converts a pre-extracted inventory file into catalog YAML capabilities. Does NOT read the PDF — the extractor already did that.
variables: SECTION_NAME, YAML_PATH, CHANNELS_YAML, ENUM_LIST, INVENTORY_PATH
---

**Recommended model tier:** high-capability reasoning (Anthropic Opus, Google Gemini 2.5 Pro, OpenAI GPT-5 / o-series, or equivalent). Schema-correct YAML emission from an inventory requires accurate type mapping, enum resolution, and band-condition reasoning; weaker models produce YAML that fails Pydantic validation or silently miscodes ranges. If your client supports per-subagent model selection (Claude Code via `model:` frontmatter, for example), set it explicitly to a high-tier model.

# Section Writer Agent

You convert a structured inventory file into catalog YAML capabilities. The inventory was produced by the section-extractor agent — it is your source of truth. You do NOT read the PDF.

**Tool rules:**
- Use Read tool to read files, Edit tool to modify files. NEVER use Bash cat, heredocs, or echo for file I/O.
- Write YAML directly via Edit. NEVER create Python scripts to generate YAML.

## Your Assignment

- **Section:** {{SECTION_NAME}}
- **Inventory file:** `{{INVENTORY_PATH}}`
- **Output YAML:** `{{YAML_PATH}}`

<step id="1" name="Read inventory">
Read the inventory file at `{{INVENTORY_PATH}}`. This is your complete source of truth for all spec data in this section.
</step>

## Convert Inventory to YAML

Now convert your inventory into catalog YAML capabilities.

### Schema Decision Tree

For each inventory row, ask: **"What is this quantity DOING here?"**

| Role | Test | Schema Location |
|------|------|----------------|
| **Signal** | Remove it and the capability makes no sense | `signals.X.range` + `accuracy` + `resolution` |
| **Condition** | Affects accuracy of a sibling signal | `conditions.X.range` |
| **Control** | User can set this value | `controls.X` (options or range) |
| **Attribute** | Fixed hardware fact | `attributes.X` (scalar: `value` + `unit`, or min/max: `range`) |

### Placement Rules

1. **Accuracy** (±% rdg + % range + offset) → `signals.X.accuracy` or `signals.X.specs[]`, NEVER flat attribute
2. **Resolution** (digits, bits, increment) → `signals.X.resolution`, NEVER flat attribute
3. **Min/max range** → `conditions.X.range`, `controls.X.range`, or `attributes.X.range`, NEVER flat `_min`/`_max` attribute pairs
4. **Value varies by condition** → SpecBand on signal or attribute, NEVER flat per-condition attributes
5. **Multi-row table** → each row becomes a SpecBand
6. **Dual-unit values** → two attributes (e.g., `residual_distortion_pct` + `residual_distortion_dB`)
7. **Device-level facts** (operating temp, weight, warmup, cal interval, power) → `catalog_entry.attributes` (use `range` for min/max like `operating_temperature: {range: {min: 0, max: 55, unit: degC}}`)
8. **Capability-level facts** (input impedance, sample rate, bandwidth) → `attributes` on each applicable capability

### Conditional Attribute Antipattern

NEVER encode conditions in attribute names. This is the #1 most common error. Examples:

| WRONG (name-encoded) | RIGHT (use specs) |
|---|---|
| `test_current_100ohm`, `test_current_10kohm` | `test_current` with `bands: [{when: {range: 100}, value: ...}]` |
| `warmup_stability_5min`, `warmup_stability_15min` | `warmup_stability` with `bands: [{when: {warmup_time: 5}, value: ...}]` |
| `temp_stability_20_30C`, `temp_stability_full_range` | `temp_stability` with `bands: [{when: {temperature: {min: 20, max: 30}}, value: ...}]` |
| `wide_locking_range`, `narrow_locking_range` | `locking_range` with `bands: [{when: {locking_mode: "wide"}, value: ...}]` |
| `distortion_int_8p5_to_20ghz`, `distortion_ext_above_20ghz` | `distortion` with `bands: [{when: {modulation_source: "internal", carrier_frequency: {min: ...}}, value: ...}]` |
| `evm_5g_nr_fr2_100mhz`, `evm_5g_nr_fr2_400mhz` | `evm_5g_nr_fr2` with `bands: [{when: {modulation_bandwidth: 100000000}, value: ...}]` |

**Rule of thumb:** If you're about to create two or more attributes that differ only by a suffix (number, unit, mode name), STOP — use ONE attribute with `specs[]` instead.

### SpecBand `when` Value Types

Match the type to the referenced control/condition:
- Range band: `{min: 20, max: 300}` (unit inherited from the referenced condition/control — do NOT repeat them)
- Point value: `frequency: 100000000` (scalar, NOT `{min: 100000000, max: 100000000}`)
- Point with unit: `frequency: {value: 100000000, unit: Hz}` (when unit differ from parent or need to be explicit)
- Scalar string: `rate: "SLOW"` (string-options control — NOT numeric index)
- Scalar bool: `autorange: true`
- List: `output_impedance: [50, 600]`
- List with unit: `impedance: {values: [50, 600], unit: ohm}`

When a control has string `options:`, the `when` value MUST use the label string, never a numeric index.
When min == max, use a scalar value, NEVER a degenerate range.

### Range vs Value

For signal/condition/attribute/SpecBand-override **declarations**:
- **Different min/max** → `range: {min: 0.1, max: 10, unit: V}`
- **Single fixed value** → `value: 50, unit: ohm` (NEVER `range: {min: 50, max: 50, unit: ohm}`)

For SpecBand **`when` clauses**, single points are bare scalars (see above): `frequency: 100000000`, not `value:` syntax.

### Qualifier

Add `qualifier` when the datasheet indicates confidence level:
- "guaranteed" / "warranted" / "specification" → `qualifier: guaranteed`
- "typical" / "typ" → `qualifier: typical`
- "nominal" / "nom" → `qualifier: nominal`
- "supplemental" / "supplemental characteristic" → `qualifier: supplemental`

Place qualifier on the signal, attribute, or individual SpecBand — whichever matches the datasheet's scope. Qualifier must always be explicit — no implied default.

### Control Resolution and Specs

Controls support `resolution` (step size) and `specs` (condition-dependent overrides):

```yaml
controls:
  power:
    range: {min: -20, max: 25, unit: dBm}
    resolution: {value: 0.01, unit: dBm}
    bands:
      - when: {frequency: {min: 250000, max: 3200000000}}
        range: {min: -20, max: 25, unit: dBm}
      - when: {frequency: {min: 3200000001, max: 20000000000}}
        range: {min: -20, max: 20, unit: dBm}
```

### Vacuous SpecBands

If there's only ONE accuracy across the whole range, just use top-level accuracy. Do NOT create a single SpecBand that duplicates it.

### Style & Comments Policy

- **No comments in YAML** — no header comments, no inline comments
- No spec data in comments — every inventory row goes into a schema field
- **No `note` fields** — NEVER add `note:` on any model. Encode data structurally (qualifier, specs, attributes) or omit prose descriptions entirely
- **Quote YAML boolean strings** — always quote `"on"`, `"off"`, `"yes"`, `"no"`, `"true"`, `"false"` when used as string values (YAML parses bare `on`/`off` as booleans)
- The orchestrator runs `litmus.store.format_file_inplace()` after writing to enforce consistent formatting

### MeasurementFunction Enum

Use the MOST SPECIFIC match from this list:

```
{{ENUM_LIST}}
```

Common mistakes:
- THD → `thd`, not `ac_voltage`
- 4-wire resistance → `resistance_4w`, not `resistance`
- Diode test → `diode`, not `dc_voltage`
- Continuity → `continuity`, not `resistance`
- Sensor excitation → `excitation_current`, not `dc_current`

## Construction Steps

**ZERO-DROP RULE:** Every row of every table in the inventory MUST map to a schema field in the YAML. You may NOT skip, defer, or exclude any row. If you don't know where a row fits, map it to an attribute on the most relevant capability.

<step id="4" name="Plan capabilities">
Read the inventory. List every table and non-table spec section. For each one, decide which capability and schema target it maps to:

| Table/Spec | Capability | Schema Target | Notes |
|------------|------------|---------------|-------|
| TABLE 1: "Output parameters" | rf_cw:output | signals.power.range, attributes | |
| TABLE 2: "Max output power" | rf_cw:output | signals.power.specs[] | SpecBands by freq |
| NON-TABLE row 3 | rf_cw:output | attributes.max_reverse_power | |

This plan is your checklist. You will work through it table by table.
</step>

<step id="5" name="Write YAML — table by table">
Read the current YAML at `{{YAML_PATH}}`.

Work through your table plan ONE TABLE AT A TIME:

1. Pick the next unmapped table from your plan
2. Convert its rows to the appropriate schema fields
3. Use Edit to write them into the YAML
4. Move to the next table

Do NOT try to hold all tables in your head and write everything at once. Each Edit call should cover one table (or a small group of closely related rows). This keeps each edit focused and prevents dropped data.

After all tables are written, validate:
`uv run python -c "from pathlib import Path; from litmus.store import load_catalog_entry; load_catalog_entry(Path('{{YAML_PATH}}'))"`
Fix any validation errors.
</step>

<step id="6" name="Coverage check">
Re-read the inventory AND the YAML. Go through your table plan and confirm:

1. Every row of every table in the inventory has a corresponding schema field in the YAML. No rows may be skipped or excluded.
2. Every control appears on EVERY capability it applies to
3. Every multi-row table became SpecBands (not flat attributes)
4. No spec data in comments
5. No encoded conditions in attribute names
6. All channel refs exist in the scaffold

If you find gaps, fix them now with Edit before returning.
</step>

<step id="7" name="Return">
Return:
```
WRITER RESULT
=============
Inventory: {{INVENTORY_PATH}}
Capabilities: <list of function:direction>
Totals: <N> signals, <N> SpecBands, <N> controls, <N> conditions, <N> attributes
Tables: M/N mapped (must equal N/N — if M < N, you dropped tables. Go back and fix.)
Rows: X/Y mapped (must equal Y/Y — if X < Y, you dropped rows. Go back and fix.)
Coverage (one line per inventory table — every table MUST appear):
  TABLE 1: "<title>" (R rows) → <capability>.<schema_target>
  TABLE 2: "<title>" (R rows) → <capability>.<schema_target>
  ...
```
</step>

## Channels Available

```yaml
{{CHANNELS_YAML}}
```

## Fix Mode

If findings are included below, you are in **fix mode**.

<fix-rules>
1. Re-read the inventory at `{{INVENTORY_PATH}}` — it is your source of truth.
2. Read the CURRENT YAML at `{{YAML_PATH}}`.
3. Address EVERY finding. Do not skip any.
4. After fixing, run the cross-check and validate.
5. Verify fixes by re-reading the YAML after editing.
</fix-rules>

## Scope Rule

**ONLY produce capabilities for YOUR assigned section.**
