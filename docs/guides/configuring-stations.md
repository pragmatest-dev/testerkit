# Configuring Stations

This guide covers station configuration for different environments.

## Basic Station Config

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  name: "Production Bench 1"
  location: "Lab A"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: power_supply
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

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Instrument type (dmm, power_supply, etc.) |
| `resource` | Yes | VISA address or connection string |
| `mock_config` | No | Values for `--mock-instruments` mode |
| `mock` | No | Always mock this instrument (even without `--mock-instruments`) |

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
# Format: USB{board}::{vendor}::{product}::{serial}::INSTR
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
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
      current: 0.1
      resistance: 1000
```

### Running in Mock Mode

```bash
pytest tests/ --station-config=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

The `--mock-instruments` flag uses mock instruments instead of real hardware. Mock values come from `mock_config` in the station, or can be overridden per-test with `_mock` in config.yaml.

## Environment Variables

Use environment variables for sensitive or environment-specific values:

```yaml
instruments:
  dmm:
    type: dmm
    resource: "${DMM_VISA_ADDRESS}"
```

```bash
export DMM_VISA_ADDRESS="TCPIP::192.168.1.100::INSTR"
pytest tests/ --station=bench_1
```

## Station Types

Define templates for station configurations:

```yaml
# stations/_base.yaml
station_types:
  voltage_tester:
    description: "Basic voltage testing station"
    instruments:
      dmm:
        type: dmm
        required: true
      psu:
        type: power_supply
        required: true

  full_test:
    description: "Complete test station"
    instruments:
      dmm:
        type: dmm
        required: true
      psu:
        type: power_supply
        required: true
      scope:
        type: oscilloscope
        required: false
```

Reference in station instances:

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  station_type: voltage_tester
  location: "Lab A"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
```

## Multiple Stations

### Production Lab

```yaml
# stations/prod_bench_1.yaml
station:
  id: prod_bench_1
  name: "Production Bench 1"
  location: "Production Floor, Bay 1"

supported_phases:
  - production

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.10.101::INSTR"
  psu:
    type: power_supply
    resource: "TCPIP::192.168.10.102::INSTR"
```

### Development Lab

```yaml
# stations/dev_bench.yaml
station:
  id: dev_bench
  name: "Development Bench"
  location: "R&D Lab"

supported_phases:
  - development
  - debug

instruments:
  dmm:
    type: dmm
    resource: "USB0::0x2A8D::0x0101::MY12345::INSTR"
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
  scope:
    type: oscilloscope
    resource: "TCPIP::192.168.1.200::INSTR"
```

### CI/CD

```yaml
# stations/ci_station.yaml
station:
  id: ci_station
  name: "CI Environment"
  description: "For automated testing with --mock-instruments"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
      current: 0.1
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
```

Run in CI:
```bash
pytest tests/ --station-config=stations/ci_station.yaml --mock-instruments --dut-serial=CI-TEST
```

## Active Fixture

Track which fixture is installed:

```yaml
station:
  id: bench_1
  active_fixture: power_board_fixture

instruments:
  # ...
```

This enables validation that the correct fixture is in place.

## Capability Declarations

Explicitly declare station capabilities:

```yaml
station:
  id: bench_1
  capabilities:
    - direction: input
      domain: voltage
      signal_types: [dc, ac]
      range:
        min: 0.001
        max: 1000
    - direction: output
      domain: voltage
      signal_types: [dc]
      range:
        max: 60
```

Usually capabilities are derived from instrument types, but explicit declaration allows customization.

## Validation

Station configuration is validated by Pydantic models when loaded:

```python
from litmus.config.loader import load_station_instance

# Raises ValidationError if config is invalid
station = load_station_instance("stations/bench_1.yaml")
print(f"Station: {station.id}")
print(f"Instruments: {list(station.instruments.keys())}")
```

Invalid configurations raise `pydantic.ValidationError` with details about what's wrong.

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
station:
  id: simple_station

instruments:
  dmm:
    type: dmm
    resource: "USB0::0x2A8D::0x0101::MY12345::INSTR"
```

### Full Production

```yaml
station:
  id: production_station
  location: "Production Floor"

instruments:
  dmm_1:
    type: dmm
    resource: "TCPIP::192.168.1.101::INSTR"
  dmm_2:
    type: dmm
    resource: "TCPIP::192.168.1.102::INSTR"
  psu:
    type: power_supply
    resource: "TCPIP::192.168.1.103::INSTR"
  eload:
    type: electronic_load
    resource: "TCPIP::192.168.1.104::INSTR"
  scope:
    type: oscilloscope
    resource: "TCPIP::192.168.1.105::INSTR"
```

## Next Steps

- [Stations Concept](../concepts/stations.md) — Understanding stations
- [Capabilities](../concepts/capabilities.md) — Capability matching
- [Adding Instruments](adding-instruments.md) — Custom drivers
