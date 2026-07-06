# Instruments

Skip this unless the test needs a **connected instrument** (`psu`, `dmm`,
`scope`, ...). A bare `verify`/`observe`/`measure` test needs none of this —
see `litmus refs show routing` (rung 0-1) and `litmus refs show tiers`
(Tier 0/1). This card is Tier 2+: a station YAML exists and the test
signature names an instrument role.

Litmus does **not** ship instrument drivers. You bring your own — PyVISA,
PyMeasure, a vendor SDK, or a plain class with `connect()`/`disconnect()`.

## Declare an instrument: station YAML

`stations/<id>.yaml` maps role names to instrument config. Each role key
(`psu`, `dmm`, ...) becomes a pytest fixture automatically — no conftest
entry needed:

```yaml
# stations/bench_01.yaml
id: bench_01
name: Bench 01
station_type: bench
instruments:
  psu:
    type: psu
    driver: drivers.PSU                     # dotted import path: module.Class
    resource: TCPIP::192.168.1.101::INSTR   # PyVISA resource string
    catalog_ref: generic_psu                # optional — see Catalog below
    mock_config:
      set_voltage: 5.0
      measure_current: 0.042
  dmm:
    type: dmm
    driver: drivers.DMM
    resource: TCPIP::192.168.1.102::INSTR
    catalog_ref: generic_dmm
    mock_config:
      measure_dc_voltage: 3.31
```

`driver` resolves via `importlib.import_module` — `drivers.PSU` means
"class `PSU` in module `drivers`" (your project's own package, or
`pymeasure.instruments.keithley.Keithley2400`, etc.). At least one of
`resource`, `driver`, or `mock: true` is required — a real-hardware entry
with none of the three raises a validation error at load.

Test signature just names the role:

```python
def test_rail_within_spec(verify, psu, dmm) -> None:
    psu.set_voltage(5.0)
    verify("v_rail", dmm.measure_dc_voltage())
```

## Unit-specific tier: `instruments/<role>.yaml` (optional)

Carries serial number, calibration cert, and identity — the "this specific
unit" facts the station file shouldn't own. Station config overrides an
asset file field-for-field where both are set; `catalog_ref` and `driver`
fall back to the asset file if the station entry omits them.

## Catalog: what a make/model can do

`catalog/<id>.yaml` describes a make/model's capabilities independent of any
project — `capabilities` (function/direction/signals/conditions/controls),
`channels` (terminals, connector, ground topology), and an optional
catalog-level `driver` default:

```yaml
# catalog/generic_dmm.yaml
id: generic_dmm
manufacturer: Generic
model: DMM
type: dmm
channels:
  "1":
    terminals: [hi, lo]
    connector: binding_post
    ground: shared
capabilities:
  - function: dc_voltage
    direction: input
    signals:
      voltage:
        range: {min: 0.001, max: 1000, unit: V}
```

Three-tier lookup for driver resolution: station `driver:` wins; if absent,
`catalog_ref` → catalog entry's `driver:` is the fallback. Same chain feeds
capability matching — `litmus discover` scans connected instruments and
tags each with a `catalog_ref` when its `*IDN?` model string matches a
catalog entry; the MCP tool `litmus_match(part_id=...)` reports which
stations' declared instruments cover a part's required capabilities
(no CLI equivalent — MCP-only).

Models: `InstrumentCatalogEntry`, `InstrumentCapability`, `Signal`,
`Condition`, `Control` in `src/litmus/models/capability.py` and
`src/litmus/models/catalog.py` — read those before hand-writing a
capability block; getting `signals`/`conditions`/`controls` disjoint
matters (overlapping keys raise a validation error).

## Mock substitution

`--mock-instruments` swaps every declared role for a mock built from its
`mock_config:` — a bare `Mock` **not** spec'd to the driver class
(`isinstance(dmm, MyDMM)` is `False` on this path). `mock_config` keys are
method names, values are static returns. Per-test overrides (different
return for one test, e.g. an OVP fault path) are a separate mechanism — see
`litmus refs show mocks`.

`mock_instruments` is also a session fixture: true when `--mock-instruments`
was passed, or `LITMUS_MOCK_INSTRUMENTS=1` is set. Mocked runs demote the
run's test-phase stamp to `"development"` — dashboards ignore the row even
though limits/markers still apply.

## Gotchas

- No `stations/<id>.yaml` resolvable and `--station` wasn't passed
  explicitly → instrument fixtures silently don't exist; a test naming
  `psu` fails with a missing-fixture error, not a clearer message.
- `--station` passed explicitly but the file isn't found → a `UserWarning`
  names the missing file (`Fix: create stations/{station_id}.yaml`).
- A station YAML that fails Pydantic validation aborts collection
  (`pytest.UsageError`) — same posture as a bad profile, not a warning.
