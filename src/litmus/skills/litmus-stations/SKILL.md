---
name: litmus-stations
description: Use when a user wants to set up the bench — declare a station's instruments and roles, wire in a bring-your-own driver, discover connected hardware, or route DUT pins to instrument channels with a fixture.
---

# Setting up a station

A station is `stations/<id>.yaml` — the bench's instrument roster, one entry
per role (`psu`, `dmm`, `scope`, ...). Litmus ships **no drivers**. Every
`driver:` you write points at a class the user already has — PyVISA, PyMeasure,
a vendor SDK, or a plain class with `connect()`/`disconnect()`.

Don't scaffold a station for a request that doesn't need one — a bare
`verify`/`observe` test needs no instrument at all (`litmus-tests`).

## 1. Pick the shape (the decision that matters)

| The user wants to… | write |
|---|---|
| declare what's on the bench (a PSU, a DMM, at these addresses) | `stations/<id>.yaml` — this skill |
| map a specific DUT pin to a specific instrument channel | `fixtures/<name>.yaml` — §4 below |
| spec what the DUT itself measures/tolerates | `litmus-parts` |
| swap real instruments for canned returns at run time | `--mock-instruments` / `litmus-mocks` |

## 2. Write the station YAML

```yaml
# stations/bench_01.yaml
id: bench_01
name: Bench 01
station_type: bench            # optional — names an abstract stations/types/*.yaml template
instruments:
  psu:
    type: psu
    driver: drivers.PSU                     # dotted path: module.Class
    resource: TCPIP::192.168.1.101::INSTR   # PyVISA resource string
    catalog_ref: generic_psu                # optional — see §5
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

Each `instruments:` key is a **role**; the pytest plugin auto-registers it as
a fixture — a test signature just names it, no conftest entry needed:

```python
def test_rail_within_spec(verify, psu, dmm) -> None:
    psu.set_voltage(5.0)
    verify("v_rail", dmm.measure_dc_voltage(), limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

One of `resource`, `driver`, or `mock: true` is required per instrument — a
real-hardware entry with none of the three fails validation at load, naming
which to set. `driver:` resolves as a dotted import path
(`importlib.import_module`) — the user's own package, or a third-party path
like `pymeasure.instruments.keithley.Keithley2400`. If `driver:` is unset but
`resource:` is a VISA string, Litmus opens it as a raw PyVISA resource instead.

## 3. Discover what's connected

```bash
litmus discover              # scan VISA/NI/serial/LXI, print *IDN? results
litmus discover --visa --json
litmus station init          # interactive: discovers, then prompts for a role per instrument
```

`litmus discover` is read-only. `litmus station init` walks each discovered
instrument, asks for a role, and writes `stations/<id>.yaml` for you.

## 4. Wire pins to instruments — a fixture (only past one bench/one DUT)

Skip this for a single DUT on a single bench remembered by hand — the
per-role `psu`/`dmm` fixtures from §2 are enough. Reach for a fixture once a
part runs on more than one bench, or a bench runs more than one part:

```yaml
# fixtures/buck_3v3_bench.yaml
id: buck_3v3_bench
part_id: buck_3v3
connections:
  vin_source: {name: vin_source, uut_pin: TP_VIN, instrument: psu, instrument_channel: CH1}
  vout_measure: {name: vout_measure, uut_pin: TP_VOUT, instrument: dmm, instrument_channel: "1"}
```

`uut_pin` references a pin key from the part spec — write the part
(`litmus-parts`) before the fixture. `part_id` scopes the fixture to one
part (`part_family` shares it across variants).

## 5. Catalog — optional capability matching

`catalog/<id>.yaml` describes a make/model independent of any project
(capabilities, channels, an optional catalog-level `driver` default).
`catalog_ref:` on a station instrument links to it, and covers `driver:`
when the station entry omits one. Skip it for a one-bench project — it
earns its keep once you're matching a part against several candidate
stations (MCP `litmus_match(part_id=...)`).

## 6. No hardware yet

`--mock-instruments` swaps every declared role for a bare `Mock`, seeded
from that role's `mock_config:` (method name → static return). That's the
station-level default; per-test overrides for one test (an OVP fault path)
are `litmus-mocks`, not this skill.

## 7. Save, validate, run

```bash
litmus validate stations/bench_01.yaml   # or: litmus validate (scans stations/, parts/, fixtures/, catalog/)
pytest --station=bench_01
pytest --mock-instruments --station=bench_01
```

MCP equivalent (agent writing on the user's behalf) — call the schema before
any save:

```python
litmus_schema(yaml_type="station")
litmus_project(action="save", type="station", id="bench_01", content={...}, project=project_root)
```

## Gotchas

- No resolvable `stations/<id>.yaml` and `--station` wasn't passed →
  an instrument-named fixture (`psu`) fails as a missing pytest fixture,
  not a clear "no station" error.
- A station or fixture YAML that fails Pydantic validation aborts test
  collection — fix the file, don't work around it.

## Deeper
Read the docs:
```bash
litmus docs show how-to/configuration/configuring-stations
litmus docs show how-to/configuration/custom-drivers
litmus docs show concepts/configuration/stations
litmus docs show concepts/configuration/fixtures
```
Sibling skills: `litmus-tests` (verbs, right-sizing), `litmus-parts` (DUT pins +
characteristics), `litmus-mocks` (per-test mock overrides), `litmus-sites`
(multi-UUT fixtures, `--site`).
