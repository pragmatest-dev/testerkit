# Stations

A **Station** is where you test — a physical bench with instruments. Station configs define what instruments are available and how to connect to them.

## Station Configuration

Station configs are YAML files in `stations/`:

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

## Instrument Configuration

Each instrument has:

| Field | Description |
|-------|-------------|
| `type` | Instrument type (dmm, scope, psu, eload, etc.) |
| `resource` | VISA address or connection string |
| `simulate` | Use simulated driver (default: false) |
| `sim_config` | Values for simulation |

### Common Instrument Types

| Type | Description | Typical Capabilities |
|------|-------------|---------------------|
| `dmm` | Digital Multimeter | voltage (DC/AC), current, resistance |
| `scope` | Oscilloscope | voltage (AC), frequency, time |
| `power_supply` | DC Power Supply | voltage output, current output |
| `eload` | Electronic Load | current sink |
| `funcgen` | Function Generator | waveform output |

## Simulated Mode

For development without hardware, use `simulate: true`:

```yaml
instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    simulate: true
    sim_config:
      voltage: 3.31
      current: 0.1
```

### Simulation Levels

Litmus supports two levels of simulation:

**1. Driver-level simulation (pyvisa-sim)**

Uses `simulate=True` on the driver. The driver sends simulated I/O through the full communication stack:

```python
from litmus.instruments import DMM

dmm = DMM("TCPIP::192.168.1.100::INSTR", simulate=True, sim_config={"voltage": 3.3})
```

**2. Interface-level mocks**

Uses mock classes that implement capability interfaces directly. No I/O overhead:

```python
from litmus.instruments import MockDMM

dmm = MockDMM(voltage=3.3)
```

### When to Use Each

| Approach | Use Case |
|----------|----------|
| `--simulate` flag | Development, CI, station without hardware |
| Mock objects | Unit tests, fast iteration |
| Real hardware | Production, calibration |

## Station Types and Instances

Litmus supports a two-level station architecture:

### Station Types (Templates)

Define abstract station requirements:

```yaml
# stations/_base.yaml
station_types:
  voltage_tester:
    description: "Station for voltage testing"
    instruments:
      dmm:
        type: dmm
        required: true
      psu:
        type: power_supply
        required: true
    capabilities:
      - direction: input
        domain: voltage
      - direction: output
        domain: voltage
```

### Station Instances (Deployments)

Concrete stations that implement a type:

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  station_type: voltage_tester
  location: "Lab A, Bench 1"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
```

## Using Stations in Tests

### Via pytest

```bash
pytest tests/ --station=bench_1 --dut-serial=SN001
```

### Via fixtures

```python
@pytest.fixture
def dmm(station):
    """Get DMM from current station."""
    return station.instruments["dmm"]

@litmus_test
def test_voltage(vector, dmm):
    return dmm.measure_voltage()
```

### Via CLI

```bash
litmus serve                    # Start UI
litmus runs                     # List recent runs
litmus show <run_id>            # Show run details
```

## Supported Test Phases

Stations can optionally declare which test phases they support:

```yaml
station:
  id: bench_1
  name: "Production Bench 1"

supported_phases:
  - validation
  - production
  - debug

instruments:
  # ...
```

This enables filtering when selecting stations for test sequences.

## Environment Variables

Station configs can reference environment variables:

```yaml
instruments:
  dmm:
    type: dmm
    resource: "${DMM_VISA_ADDRESS}"
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
station:
  id: ci_station
  name: "CI Environment"
  description: "Fully simulated for CI/CD"

instruments:
  dmm:
    type: dmm
    resource: "SIM::DMM"
    simulate: true
    sim_config:
      voltage: 3.3
      current: 0.1
  psu:
    type: power_supply
    resource: "SIM::PSU"
    simulate: true
```

## Next Steps

- [Capabilities](capabilities.md) — Understanding what stations can do
- [Fixtures](fixtures.md) — Mapping DUT pins to instruments
- [Configuration Reference](../reference/configuration.md) — YAML schema details
