# Configuring Stations

This guide covers station configuration for different environments.

## Basic Station Config

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

## Station Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier |
| `name` | No | Display name |
| `location` | No | Physical location |
| `description` | No | Description |
| `supported_phases` | No | Which test phases this station supports |

## Instrument Fields

Per [`StationInstrumentConfig`](../../reference/data/models.md) in `src/litmus/models/station.py`:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Instrument type (dmm, psu, etc.) |
| `driver` | At least one of driver/resource (unless `mock: true`) | Dotted import path to the driver class (e.g. `pymeasure.instruments.keysight.Keysight34461A`) |
| `resource` | At least one of driver/resource (unless `mock: true`) | VISA address or connection string |
| `catalog_ref` | No | ID of a catalog entry that declares this instrument's capabilities |
| `channels` | No | Dict of named channel→port mappings |
| `description` | No | Human-readable description |
| `mock` | No | Always mock this instrument (even without `--mock-instruments`). Default `false` |
| `mock_config` | No | Return values when running in mock mode |

## VISA Addresses

### TCP/IP (LAN)

```yaml
resource: "TCPIP::192.168.1.100::INSTR"
resource: "TCPIP::192.168.1.100::5025::SOCKET"  # With port
resource: "TCPIP::dmm.lab.local::INSTR"  # DNS name
```

### GPIB

```yaml
resource: "GPIB0::5::INSTR"   # Board 0, address 5
resource: "GPIB1::12::INSTR"  # Board 1, address 12
```

### USB

```yaml
resource: "USB0::0x2A8D::0x0101::MY12345::INSTR"
# Format: USB{board}::{vendor}::{part}::{serial}::INSTR
```

### Serial

```yaml
resource: "ASRL/dev/ttyUSB0::INSTR"  # Linux
resource: "ASRLCOM3::INSTR"          # Windows
```

## Mock Mode Configuration

Configure default values for `--mock-instruments` mode:

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_dc_voltage: 3.31
      measure_current: 0.1
      measure_resistance: 1000
```

### Running in Mock Mode

```bash
pytest tests/ --station=stations/bench_1.yaml --mock-instruments --uut-serial=SIM001
```

The `--mock-instruments` flag uses mock instruments instead of real hardware. Mock values come from `mock_config` in the station, or can be overridden per-test in the sidecar YAML next to your test file (`tests/test_*.yaml`).

## Station Types

A station type is a template — one YAML file per type under `stations/types/<id>.yaml`. It declares the instrument roles (and their `type:`) every station of that type must provide, plus the catalog capability ids the type advertises:

```yaml
# stations/types/voltage_tester.yaml
id: voltage_tester
description: "Basic voltage testing station"
instruments:
  dmm:
    type: dmm
  psu:
    type: psu
capabilities:
  - keysight.34461a.dc_voltage
  - keysight.e36312a.dc_source
```

```yaml
# stations/types/full_test.yaml
id: full_test
description: "Complete test station"
instruments:
  dmm:
    type: dmm
  psu:
    type: psu
  scope:
    type: scope
```

There is no `required:` field on an instrument entry — every role listed under `instruments:` is required, and any role not listed is omitted by definition. `capabilities:` is a list of catalog capability ids (strings), not inline `Capability` dicts.

Reference in station instances:

```yaml
# stations/bench_1.yaml
id: bench_1
station_type: voltage_tester
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

## Multiple Stations

### Production Lab

```yaml
# stations/prod_bench_1.yaml
id: prod_bench_1
name: "Production Bench 1"
location: "Production Floor, Bay 1"

supported_phases:
  - production

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.10.101::INSTR"
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "TCPIP::192.168.10.102::INSTR"
```

### Development Lab

```yaml
# stations/dev_bench.yaml
id: dev_bench
name: "Development Bench"
location: "R&D Lab"

supported_phases:
  - development
  - debug

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "USB0::0x2A8D::0x0101::MY12345::INSTR"
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
  scope:
    type: scope
    driver: drivers.scope.MyScope
    resource: "TCPIP::192.168.1.200::INSTR"
```

### CI/CD

```yaml
# stations/ci_station.yaml
id: ci_station
name: "CI Environment"
description: "For automated testing with --mock-instruments"

instruments:
  dmm:
    type: dmm
    mock: true
    catalog_ref: generic_dmm
    mock_config:
      measure_dc_voltage: 3.31
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

## Selecting a fixture at run time

Stations don't pin a fixture themselves. The active fixture is selected by `--fixture=...` on the pytest command line (or by a profile that sets it). The plugin validates that the fixture's `part_id` / `part_family` matches the active part spec before any test runs.

```bash
pytest tests/ \
  --station=bench_1 \
  --fixture=fixtures/power_board_fixture.yaml \
  --uut-serial=SN001
```

See [Fixtures](../../concepts/configuration/fixtures.md) for the pin-to-instrument mapping model.

## Capability Declarations

A station instance (`stations/<id>.yaml`) does not carry a `capabilities:` field — capabilities are derived from each instrument's catalog entry at load time (the matcher walks `station.instruments → instrument.catalog_ref → catalog/<...>.yaml → capabilities[]`).

If you want to advertise a fixed capability set as part of a *type* (so concrete stations of that type are guaranteed to provide it), declare it on the **station type**, not on the instance, as a list of catalog capability ids:

```yaml
# stations/types/voltage_bench.yaml
id: voltage_bench
description: "Generic DC + AC voltage bench"
instruments:
  dmm: {type: dmm}
  psu: {type: psu}
capabilities:
  - keysight.34461a.dc_voltage
  - keysight.34461a.ac_voltage
  - keysight.e36312a.dc_source
```

To customize the capabilities a specific instrument provides, edit that instrument's catalog YAML (`catalog/<vendor>/<model>.yaml`) — not the station file.

## Validation

Station configuration is validated by Pydantic models when loaded:

```python
from litmus.store import load_station

# Raises pydantic.ValidationError if config is invalid
station = load_station("stations/bench_1.yaml")
print(f"Station: {station.id}")
print(f"Instruments: {list(station.instruments.keys())}")
```

Invalid configurations raise `pydantic.ValidationError` with details about what's wrong.

## Shared Instruments (Multi-UUT)

When a fixture defines multiple **slots** (parallel UUT positions — see [Multi-UUT testing](../execution/multi-uut-testing.md)), instruments referenced by more than one slot are automatically detected as **shared**. The orchestrator connects shared instruments once and hosts them via an internal RPC server (`InstrumentServer` in `src/litmus/instruments/server.py`); worker subprocesses access them through transparent proxy objects. Tests never know the difference.

### Per-Resource Locking

Locking is keyed on the instrument's **resource string** (e.g., VISA address), not the role name:

- **Same resource** → same driver session → same lock → serialized access
- **Different resources** → different sessions → independent locks → parallel access
- **Switches** → concurrent access (relay operations are atomic)

Two roles pointing at the same physical instrument share a lock automatically:

```yaml
instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"   # ← lock key
  scope:
    type: scope
    resource: "TCPIP::192.168.1.100::INSTR"   # ← same resource = same lock
```

### Automatic Server Setup

No special flags are needed. When the fixture defines multiple slots that share an instrument role, the orchestrator automatically:

1. Connects the shared instruments once in the orchestrator process
2. Starts an internal RPC server to serve them
3. Worker subprocesses get transparent `RemoteInstrumentProxy` objects

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
  matrix:
    type: switch
    driver: drivers.switch.RelayMatrix
    resource: "TCPIP::192.168.1.106::INSTR"
```

## Best Practices

1. **Use descriptive IDs** — `prod_bench_1` not `station1`
2. **Include location** — Helps operators find equipment
3. **Define supported phases** — Prevents wrong usage
4. **Use environment variables** — For IP addresses in different environments
5. **Create CI station** — Fully simulated for automated tests
6. **Version control** — Track station changes

## Common Configurations

### Single DMM

```yaml
id: simple_station

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "USB0::0x2A8D::0x0101::MY12345::INSTR"
```

### Full Production

```yaml
id: production_station
location: "Production Floor"

instruments:
  dmm_1:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.101::INSTR"
  dmm_2:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.102::INSTR"
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "TCPIP::192.168.1.103::INSTR"
  eload:
    type: eload
    driver: drivers.eload.MyELoad
    resource: "TCPIP::192.168.1.104::INSTR"
  scope:
    type: scope
    driver: drivers.scope.MyScope
    resource: "TCPIP::192.168.1.105::INSTR"
```

## Next Steps

- [Stations Concept](../../concepts/configuration/stations.md) — Understanding stations
- [Capabilities](../../concepts/configuration/capabilities.md) — Capability matching
- [Custom drivers](custom-drivers.md) — Build a non-VISA driver
