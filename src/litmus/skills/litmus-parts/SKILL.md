---
name: litmus-parts
description: Use when a user wants to spec the DUT — capture its documented characteristics (nominal + accuracy band), pin map, or a datasheet limit as reusable part YAML.
---

# Specing a part

A part is `parts/<id>.yaml` — the DUT's documented characteristics (what a
`dc_voltage` output is nominally, with what accuracy, at which pin) plus its
pin map. It exists so a limit and a pin mapping live once and get referenced
by name from every test, instead of retyped per test file.

Don't scaffold `parts/` for a one-off bench check — an inline `limit={...}`
or a sidecar `limits:` entry (`litmus-tests`) covers that. Reach for a part
spec once several tests share a limit, or a non-developer needs to edit a
spec value without touching test code.

## 1. The boundary (read this before writing anything)

A **characteristic** is the DUT's documented spec — the datasheet nominal and
its accuracy band. A **test limit** is what a test actually checks a
measurement against, and is usually *tighter* than the characteristic via a
guardband. **Defining** the characteristic is this skill. **Using** it —
`{characteristic: rail_3v3, guardband_pct: 5}` in a test's limit config — is
`litmus-tests`. Don't duplicate the guardband math here; write the
characteristic, then hand off.

## 2. Write the part YAML

```yaml
# parts/buck_3v3.yaml
id: buck_3v3
part_number: DEMO-BUCK-3V3         # printed/scanned identifier; stamped as
name: Demo 3.3 V Buck Converter    # uut_part_number on every run
revision: A

pins:
  TP_VIN:  {name: TP1, net: VIN_5V}
  TP_VOUT: {name: TP2, net: VOUT_3V3}
  TP_GND:  {name: TP3, net: GND, role: ground}

characteristics:
  rail_3v3:
    function: dc_voltage      # MeasurementFunction enum
    direction: output          # the DUT provides this; input = DUT receives it
    unit: V
    pin: TP_VOUT
    bands:
      - when: {temperature: {min: 0, max: 50}}
        value: 3.3
        accuracy: {pct_reading: 3.0}
      - when: {temperature: {min: 50, max: 85}}
        value: 3.3
        accuracy: {pct_reading: 5.0}   # wider tolerance at high temp
```

`function` and `direction` are required on every characteristic. At least
one physical interface is required — `pin` (single), `pins` (a list or a
range string like `"GPIO[0:7]"`), `net`, or `signal_group`; `pin` wins if
more than one is set. `bands:` is an ordered list — the first `when:` clause
that matches the active vector params wins; a band with no `when:` is the
catch-all and should sit last.

## 3. Selecting the active part

```bash
pytest --part buck_3v3
pytest --uut-part-number DEMO-BUCK-3V3
```

Resolution order (first match wins): `--part <id-or-path>` →
`--uut-part-number <pn>` (matched against `part_number:`) → the single file
in `parts/` if there's exactly one → no active part (bringup tier). Two or
more `parts/*.yaml` files with neither flag set is a `UsageError` — a Rev-B
variant needs an explicit selector.

## 4. Product families — variants share a base

```yaml
# parts/buck_3v3_industrial.yaml
id: buck_3v3_industrial
base: buck_3v3           # inherits pins + characteristics from the parent
characteristics:
  rail_3v3:
    bands:
      - value: 3.3
        accuracy: {pct_reading: 1.0}   # tighter accuracy on this SKU
```

Use `base:` when only a few characteristics differ across an otherwise
identical family — the variant overrides just those sections.

## 5. Save and validate

```bash
litmus validate parts/buck_3v3.yaml
litmus validate                         # scans parts/, stations/, fixtures/, catalog/
```

MCP equivalent (agent writing on the user's behalf) — call the schema before
any save:

```python
litmus_schema(yaml_type="part")
litmus_project(action="save", type="part", id="buck_3v3", content={...}, project=project_root)
```

## Gotchas

- Omitting both `function` and `direction` on a characteristic fails
  validation immediately — both are required, no defaults.
- A characteristic with no physical interface (`pin`/`pins`/`net`/
  `signal_group`) fails validation at load, not at test time.
- `bands[].when` keys must match a sibling `signals`/`conditions`/`controls`
  name *if any of those are declared* on the characteristic — a bare
  `bands:` list with no siblings (as in the example above) skips that check.

## Deeper
Read the docs:
```bash
litmus docs show concepts/configuration/parts
litmus docs show concepts/configuration/capabilities
```
Sibling skills: `litmus-tests` (guardbanding a characteristic into a test
limit — the other half of this skill's boundary), `litmus-stations` (pin
routing via `fixtures/<name>.yaml`, which references this part's pin keys).
