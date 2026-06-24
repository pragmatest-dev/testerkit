# Stations

A **Station** is where you test — a physical bench with instruments. Station configs define what instruments are available and how to connect to them.

## Station Configuration

Station configs are YAML files in `stations/`:

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Production Bench 1"
location: "Lab A"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"

  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
```

## Instrument Configuration

Each instrument has:

| Field | Description |
|-------|-------------|
| `type` | Instrument type (dmm, scope, psu, eload, etc.) |
| `driver` | Import path to the driver class (PyMeasure / PyVISA / vendor) |
| `resource` | VISA address or connection string |
| `mock_config` | Canned return values used in `--mock-instruments` mode |
| `mock` | Force mock mode for this instrument |

Instruments can be shared across multiple UUT slots in a multi-UUT fixture. Litmus connects a shared instrument once and lets each UUT's test use it one at a time, so two tests never drive the same instrument at the same moment. Sharing is detected automatically — no extra flags. See [Configuring Stations](../../how-to/configuration/configuring-stations.md#shared-instruments-multi-uut) for details.

### Common Instrument Types

| Type | Description | Typical Capabilities |
|------|-------------|---------------------|
| `dmm` | Digital Multimeter | voltage (DC/AC), current, resistance |
| `scope` | Oscilloscope | voltage (AC), frequency, time |
| `psu` | DC Power Supply | voltage output, current output |
| `eload` | Electronic Load | current sink |
| `funcgen` | Function Generator | waveform output |

## Mock Mode

For development without hardware, use `--mock-instruments`:

```bash
pytest tests/ --station=stations/bench_1.yaml --mock-instruments --uut-serial=SIM001
```

Configure mock values in the station:

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_dc_voltage: 3.31
      measure_current: 0.1
```

### When to Use Mock Mode

| Approach | Use Case |
|----------|----------|
| `--mock-instruments` flag | Development, CI, station without hardware |
| Real hardware | Production, calibration |

## Station Types and Instances

A station can be split into a reusable template and the actual bench that fills it in:

### Station Types (Templates)

List the instrument roles and drivers a station needs:

```yaml
# stations/types/voltage_tester.yaml
id: voltage_tester
description: "Station for voltage testing"
instruments:
  dmm: {type: dmm, driver: pymeasure.instruments.keysight.Keysight34461A}
  psu: {type: psu, driver: pymeasure.instruments.keysight.KeysightE36312A}
capabilities:                                # catalog capability ids
  - keysight.34461a.dc_voltage
  - keysight.e36312a.dc_source
```

One file per station type under `stations/types/`. Every role listed under `instruments:` is required (there is no `required:` field). `capabilities:` is a list of catalog capability id strings — not inline `Capability` dicts.

### Station Instances (Real Benches)

Concrete stations that implement a type:

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Bench 1"
station_type: voltage_tester
location: "Lab A, Bench 1"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
```

### Instrument aliases per test

Station configs define the physical instrument inventory. You can override which station instrument a fixture name points to for a specific test or class — without editing the station config — using a per-test override file. See [Writing Tests](../../how-to/execution/writing-tests.md) for details.

## Using Stations in Tests

### Via pytest

```bash
pytest tests/ --station=bench_1 --uut-serial=SN001
```

### Via fixtures

```python
def test_voltage(dmm, measure):
    """Each instrument role in the station config is available as a fixture (here: dmm)."""
    measure("voltage", dmm.measure_voltage())
```

### Via CLI

```bash
litmus serve                    # Start UI
litmus runs                     # List recent runs
litmus show <run_id>            # Show run details
```

## Supported Test Phases

Stations can optionally declare which test phases they support, via `supported_phases`. This lets you filter stations when selecting one for a run or profile (see [Profiles](../../how-to/execution/profiles.md)):

```yaml
id: bench_1
name: "Production Bench 1"

supported_phases:
  - validation
  - production
  - debug

instruments:
  # ...
```

## Multiple Stations

Large test environments may have multiple stations:

```
stations/
├── _base.yaml           # Station type definitions
├── bench_1.yaml         # Production bench 1
├── bench_2.yaml         # Production bench 2
├── debug_bench.yaml     # Debug station
└── ci_station.yaml      # CI (simulated) station
```

### CI Station Example

```yaml
# stations/ci_station.yaml
id: ci_station
name: "CI Environment"
description: "For CI/CD with --mock-instruments"

instruments:
  dmm:
    type: dmm
    mock: true
    catalog_ref: generic_dmm
    mock_config:
      measure_dc_voltage: 3.3
      measure_current: 0.1
  psu:
    type: psu
    mock: true
    catalog_ref: generic_psu
    mock_config:
      measure_voltage: 5.0
```

Run in CI:
```bash
pytest tests/ --station=stations/ci_station.yaml --mock-instruments --uut-serial=CI-TEST
```

## Next Steps

- [Capabilities](capabilities.md) — Understanding what stations can do
- [Fixtures](fixtures.md) — Mapping UUT pins to instruments
- [Configuration Reference](../../reference/configuration.md) — YAML schema details
