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
| `resource` | VISA address or connection string |
| `mock_config` | Values for `--mock-instruments` mode |
Instruments can be shared across multiple DUT slots in a multi-DUT fixture. When shared, the orchestrator connects them once and serves them to worker subprocesses via an `InstrumentServer` (an internal RPC server that lets multiple test workers share one physical instrument — TCP with per-resource locking). No special flags needed — sharing is detected automatically from the fixture topology. See [Configuring Stations](../how-to/configuring-stations.md#shared-instruments-multi-dut) for details.

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
pytest tests/ --station=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

Configure mock values in the station:

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
      current: 0.1
```

### When to Use Mock Mode

| Approach | Use Case |
|----------|----------|
| `--mock-instruments` flag | Development, CI, station without hardware |
| Real hardware | Production, calibration |

## Station Types and Instances

Litmus supports a two-level station architecture:

### Station Types (Templates)

Define abstract station requirements:

```yaml
# stations/types/voltage_tester.yaml
id: voltage_tester
description: "Station for voltage testing"
instruments:
  dmm: {type: dmm}
  psu: {type: psu}
capabilities:                                # catalog capability ids
  - keysight.34461a.dc_voltage
  - keysight.e36312a.dc_source
```

One file per station type under `stations/types/`. Every role listed under `instruments:` is required (there is no `required:` field). `capabilities:` is a list of catalog capability id strings — not inline `Capability` dicts.

### Station Instances (Deployments)

Concrete stations that implement a type:

```yaml
# stations/bench_1.yaml
id: bench_1
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

Station configs define the physical instrument inventory. A sidecar YAML can optionally remap fixture names to different station instruments on a per-test or per-class basis via marker fields. See [Writing Tests](../how-to/writing-tests.md) for details.

## Using Stations in Tests

### Via pytest

```bash
pytest tests/ --station=bench_1 --dut-serial=SN001
```

### Via fixtures

```python
def test_voltage(dmm, logger):
    """Instrument roles from station config are auto-registered as fixtures."""
    logger.measure("voltage", dmm.measure_voltage())
```

### Via CLI

```bash
litmus serve                    # Start UI
litmus runs                     # List recent runs
litmus show <run_id>            # Show run details
```

## Supported Test Phases

Stations can optionally declare which test phases (`test_phase` is a station-level setting selecting the workflow phase — development, validation, production — and gating mocks; see [how-to/profiles](../how-to/profiles.md)) they support:

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

This enables filtering when selecting stations for a given test run or profile.

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
      voltage: 3.3
      current: 0.1
  psu:
    type: psu
    mock: true
    catalog_ref: generic_psu
    mock_config:
      voltage: 5.0
```

Run in CI:
```bash
pytest tests/ --station=stations/ci_station.yaml --mock-instruments --dut-serial=CI-TEST
```

## Next Steps

- [Capabilities](capabilities.md) — Understanding what stations can do
- [Fixtures](fixtures.md) — Mapping DUT pins to instruments
- [Configuration Reference](../reference/configuration.md) — YAML schema details
