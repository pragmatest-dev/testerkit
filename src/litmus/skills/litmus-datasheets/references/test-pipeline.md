---
name: datasheet-to-test
description: Create hardware tests from a part datasheet using Litmus MCP tools. Guides through spec extraction, station setup, and test generation with step-by-step approval.
---

# Datasheet to Test Workflow

<overview>
Create hardware tests from a part datasheet using Litmus.

Flow: Datasheet → Part Spec → Station → Tests → Results
         1           2            3         4        5

This is a COLLABORATIVE workflow — the user approves at every gate.
</overview>

<rules>
- Execute every phase in order. No skipping. No reordering.
- STOP and wait for user approval at every gate. Never assume approval.
- Be a guide, not a form. Use contextual, knowledgeable questions specific to what you found — not generic boilerplate.
- Present choices as numbered lists at the END of your message.
- ALWAYS use ask_user_input_v0 (or AskUserQuestion in Claude Code) at approval gates — never present options as text like [A]pprove [E]dit.
- Pass project= to ALL MCP calls after init.
- Part characteristics use the full Capability schema (signals, conditions, controls, attributes). See docs/reference/catalog/schema.md.
</rules>

<tools>
| Tool | Purpose |
|------|---------|
| litmus_project(action="init", path="...") | Initialize project, returns project_root |
| litmus_project(action="save", type="...", id="...", content={...}, project=...) | Save part/station/test |
| litmus_project(action="read", path="...", project=...) | Read files or templates |
| litmus_match(part_id, station_id, project) | Check compatibility |
| litmus_run(test="...", station="...", serial="...", project=...) | Execute tests |
| litmus_open(type="...", id="...") | Get browser URL for viewing/editing |
| litmus_project(action="lookup_enum", id="FRES") | Resolve datasheet abbreviation to enum value |
| litmus_project(action="enum_reference") | Full enum abbreviation table (markdown) |
| litmus_discover() | Scan for VISA instruments |
</tools>

---

<phase id="1" name="Parse Datasheet">

Goal: Extract electrical characteristics, pins, and test conditions from the datasheet.

<step id="1.1">
Ask the user where to create the project — suggest ~/litmus-<part_number> but let them choose.
</step>

<step id="1.2">
Initialize project: litmus_project(action="init", path="<user's chosen path>")
</step>

<step id="1.3">
Read the datasheet file the user provides.
</step>

<step id="1.4">
Use litmus_project(action="lookup_enum", id="...") to resolve datasheet abbreviations
(e.g. "FRES" → resistance_4w, "DCV" → dc_voltage) to correct MeasurementFunction enum values.
</step>

<step id="1.5">
Extract key information:
- Part ID, name, description
- Pin definitions (name, role, net)
- Electrical characteristics (voltage, current, power, timing)
- Test conditions (temperature, load, input voltage)
- Performance specs with limits (nominal, min, max, tolerance)

Pin roles: power (supply/output rails), ground (return/reference),
signal (measured/stimulated, default), reference (voltage ref, not driven).
</step>

<step id="1.6">
Show the user:
1. Part summary with part number, name, datasheet info
2. Pin table (name, role, net, purpose)
3. Characteristics table (name, function, direction, nominal value, test conditions)
4. Confidence assessment (0-100%, list any ambiguities or uncertain specs)
5. A contextual follow-up question specific to what you found
</step>

<gate id="1">
Present choices to the user via ask_user_input_v0 / AskUserQuestion.
Do NOT proceed until the user responds.
Emit: <gate-result phase="1" action="approved|revised" />
</gate>

</phase>

---

<phase id="2" name="Save Part Spec">

Goal: Save the extracted spec and let user refine it.

<step id="2.1">
Show the draft spec structure (YAML preview).
Highlight any uncertainties or missing fields.
Use ``litmus_project(action="schema", type="part")`` to fetch the live
part schema (server-side Pydantic). Characteristics use the
Capability schema — same four dicts (signals, conditions, controls,
attributes) and SpecBand matching as catalog entries.
</step>

<step id="2.2">
Save with litmus_project(action="save", type="part", ...) — schema is validated server-side.
</step>

<step id="2.3">
End with specific observations about the spec — missing guardbands, additional
testable specs you noticed, anything that looks off.
Ask a CONTEXTUAL question, e.g.:
- "I notice the efficiency spec varies with load. Should we test at all three load points?"
- "The thermal limits assume natural convection. Are you adding a heatsink?"
</step>

<gate id="2">
Present choices to the user via ask_user_input_v0 / AskUserQuestion.
Do NOT proceed until the user responds.
Emit: <gate-result phase="2" action="approved|revised" />
</gate>

</phase>

---

<phase id="2b" name="Recommend Instruments">

Goal: Find catalog instruments that can measure/source the extracted characteristics.

<step id="2b.1">
Consider passive components first: Not every UUT pin needs a programmable instrument.
A power resistor or voltage divider may suffice for fixed operating points.
Only recommend programmable instruments when the test needs dynamic control.
</step>

<step id="2b.2">
Call litmus_match(part_id="<part_id>", project=project_root) —
the platform derives requirements from the saved part characteristics automatically.
Do NOT build requirements manually.
</step>

<step id="2b.3">
If litmus_match returns catalog recommendations, present them.
If the catalog is empty or has no matches, ask the user what instruments they have:

"What test equipment do you have available?"

Handle responses:
- **Specific model** (e.g., "Keysight 34461A") → Use the scaffold path (`references/scaffold.md`) to create entry
- **Generic type** (e.g., "some DMM", "a power supply") → Use generic_dmm, generic_psu, etc.
- **Nothing yet** → Use generics for all required types, explain this is for planning/mocking

Generic instruments are in src/litmus/catalog/generic/:
- generic_dmm, generic_psu, generic_oscilloscope, generic_eload

These provide baseline capabilities sufficient for mocked testing and test development.
Real instruments can be added later.
</step>

<step id="2b.4">
For any instruments not in catalog, present options:
1. Use the scaffold path (`references/scaffold.md`) (fast, uses Claude's knowledge of common instruments)
2. Use generic_{type} template (fastest, approximate capabilities)
3. Use the catalog pipeline (`references/catalog-pipeline.md`) with PDF (thorough, for exact specs)

Recommend option 1 for well-known instruments, option 2 for unknowns or "just get started".
</step>

<gate id="2b">
Let the user pick instruments before generating station config.
Present choices via ask_user_input_v0 / AskUserQuestion.
Do NOT proceed until the user responds.
Emit: <gate-result phase="2b" action="approved|revised" />
</gate>

</phase>

---

<phase id="3" name="Create Station Config">

Goal: Configure the test station with instruments and mock values.

<step id="3.1">
Use the instruments selected in Phase 2b, or use litmus_discover().
</step>

<step id="3.2">
Build station config with realistic mock values — schema is
validated server-side. Use ``litmus_project(action="schema", type="station")``
for the live shape.
</step>

<step id="3.3">
Show config for approval.
</step>

<gate id="3">
Present choices via ask_user_input_v0 / AskUserQuestion.
Do NOT proceed until the user responds.
Emit: <gate-result phase="3" action="approved|revised" />
</gate>

</phase>

---

<phase id="4" name="Generate Tests">

Goal: Create pytest test code that exercises all characteristics.

<step id="4.1">
Generate **pytest-native** test code. Tests are plain pytest — no decorator, no base class. Use the Litmus fixtures (`context`, `verify`, `observe`) and the single `litmus_limits` marker for inline limits. Sweeps use native `@pytest.mark.parametrize` or the `litmus_sweeps` marker; see the project's `examples/` chapters for full patterns.

Skeleton to follow:

```python
# tests/test_<part>.py
import pytest

class TestRails:
    @pytest.mark.parametrize("load", [0.1, 0.4])
    @pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
    def test_output_voltage(self, vin, load, context, verify, psu, dmm, uut_load):
        if context.changed("vin"):
            psu.set_voltage(vin)
        uut_load.set(load)
        verify("output_voltage", dmm.measure_dc_voltage())
```

Notes for good generation:
- Use `verify(name, v)` for judgment-bearing measurements — limits resolve from active part/sidecar/profile; raises `LimitFailure` on out-of-band, `MissingLimitError` if no limit configured
- Use `observe(name, v)` for record-only measurements and evidence (setup readouts, characterization, captures) — no judgment, never raises. Routes by shape: scalars land inline, arrays / `Waveform` go to ChannelStore, blobs / images go to FileStore as a `file://` artifact auto-linked from the run
- Use `stream(name, sample)` to push one time-series sample to ChannelStore across a sweep (e.g. a rail logged under increasing load)
- `observe` / `verify` / `stream` are the three test-author verbs — see `litmus docs show concepts/data/three-verbs`
- Use `context.changed(k)` in parametrized sweeps to skip expensive reconfig
- Prefer native `@pytest.mark.parametrize` for code-owned sweeps; use sidecar `sweeps:` for operator-edited sweeps
</step>

<step id="4.2">
Create a **sidecar YAML** (`tests/test_<part>.yaml`) with any combination of `sweeps:`, `limits:`, `mocks:`, `characteristics:`. Sidecar is for operator-editable values; inline markers are for values that belong with the code. Limit shapes: `{low, high, unit, nominal?, comparator?}` for an explicit band, or `{characteristic: <id>, tolerance_pct: <n>}` to bind to a part characteristic.

Example:

```yaml
# tests/test_<part>.yaml
sweeps:
  - {vin: [4.5, 5.0, 5.5]}
  - {load: [0.1, 0.4, 0.8]}
limits:
  efficiency:     {low: 55, high: 100, unit: "%"}
  output_voltage: {characteristic: "output_voltage"}    # delegates to part spec
mocks:
  - target: dmm.measure_dc_voltage
    return_value: 3.3
```
</step>

<step id="4.3">
Show both files for review. MUST create BOTH files (test .py AND sidecar .yaml unless all config lives in markers).
</step>

<gate id="4">
Present choices via ask_user_input_v0 / AskUserQuestion.
Do NOT proceed until the user responds.
Emit: <gate-result phase="4" action="approved|revised" />
</gate>

</phase>

---

<phase id="5" name="Execute and Analyze">

Goal: Run the tests and help analyze results.

<step id="5.1">
Confirm test execution parameters with user.
</step>

<gate id="5-pre">
Present run parameters via ask_user_input_v0 / AskUserQuestion.
Do NOT execute until the user confirms.
Emit: <gate-result phase="5-pre" action="confirmed" />
</gate>

<step id="5.2">
Run with litmus_run(test="tests/test_x.py", station="station_id", serial="SERIAL", project=project_root).
</step>

<step id="5.3">
Show results table, analyze, suggest next steps.
</step>

<checkpoint phase="5">
Emit: <phase-complete id="5" />
</checkpoint>

</phase>

---

<reference name="Key Rules">
- mock_config keys are method names (e.g., measure_voltage, measure_current)
- Standard Python math: Instruments return float. Use standard arithmetic.
- Characteristics: use ``litmus_project(action="schema", type="capability")`` to discover valid MeasurementFunction values. Set `function:` + `direction:` (input/output).
- Instrument fixtures are auto-registered from the active station's `instruments:` dict — no conftest.py boilerplate. Tests use role names (e.g. `dmm`, `psu`) as fixture parameters directly.
</reference>
