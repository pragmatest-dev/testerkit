# Step 7: Real Instruments

**Goal:** Connect to real hardware with the ability to simulate when unavailable.

## What You'll Build

A test that works with real instruments OR in simulation mode using the same code.

## Station Configuration

Define your test station (the bench where you test):

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  name: "Production Bench 1"
  location: "Lab A, Position 1"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"

  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
```

This defines:
- A station identity and location
- A DMM at a TCP/IP address
- A PSU on GPIB

## The instruments Fixture

When you run with `--station-config`, Litmus provides an `instruments` fixture:

```python
# tests/test_power.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Access instruments by name from station config."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    psu.set_voltage(5.0)
    psu.enable_output()

    return dmm.measure_voltage()
```

Run with:
```bash
pytest tests/ --station-config=stations/bench_1.yaml --dut-serial=SN001
```

## Running in Simulation Mode

When hardware isn't available, add `--simulate`:

```bash
pytest tests/ --station-config=stations/bench_1.yaml --simulate --dut-serial=SIM001
```

The **same test code** works in both modes.

## How Simulation Works

When `--simulate` is set:
1. Drivers use pyvisa-sim instead of real I/O
2. Responses come from `sim_config` values
3. No hardware required

Configure simulation values in the station:

```yaml
# stations/bench_1.yaml
instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    sim_config:
      voltage: 3.31       # Value returned in simulation
      current: 0.1
```

## The simulate Fixture

Access the simulation flag in custom fixtures:

```python
# tests/conftest.py
import pytest
from litmus.instruments import DMM

@pytest.fixture
def my_dmm(simulate):
    """Custom DMM fixture that respects --simulate flag."""
    with DMM(
        "TCPIP::192.168.1.100::INSTR",
        simulate=simulate,
        sim_config={"voltage": 3.31}
    ) as d:
        yield d
```

The `simulate` fixture is `True` when `--simulate` is passed.

## Station for CI/CD

Create a fully-simulated station for CI:

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
    simulate: true         # Always simulate
    sim_config:
      voltage: 3.31

  psu:
    type: power_supply
    resource: "SIM::PSU"
    simulate: true
    sim_config:
      voltage: 5.0
      current: 0.1
```

Run in CI:
```bash
pytest tests/ --station-config=stations/ci_station.yaml --dut-serial=CI-TEST
```

## VISA Address Formats

| Type | Format | Example |
|------|--------|---------|
| TCP/IP | `TCPIP::host::INSTR` | `TCPIP::192.168.1.100::INSTR` |
| GPIB | `GPIB0::address::INSTR` | `GPIB0::5::INSTR` |
| USB | `USB0::vid::pid::serial::INSTR` | `USB0::0x2A8D::0x0101::MY12345::INSTR` |
| Serial | `ASRL/dev/ttyUSB0::INSTR` | `ASRL/dev/ttyUSB0::INSTR` |

## Discovering Instruments

Find connected instruments:

```bash
python -c "import pyvisa; rm = pyvisa.ResourceManager(); print(rm.list_resources())"
```

Or use the Litmus MCP tool:
```
litmus_discover()
```

## Two Levels of Simulation

### Driver-Level (simulate=True)

Uses pyvisa-sim. The driver sends I/O through the communication stack:

```python
from litmus.instruments import DMM

dmm = DMM(
    "TCPIP::192.168.1.100::INSTR",
    simulate=True,
    sim_config={"voltage": 3.3}
)
```

**Use for:** Testing driver logic, realistic timing

### Interface-Level (MockDMM)

Bypasses I/O entirely:

```python
from litmus.instruments import MockDMM

dmm = MockDMM(voltage=3.3)
```

**Use for:** Unit tests, fast iteration, CI

## Complete Example

**stations/bench_1.yaml:**
```yaml
station:
  id: bench_1
  name: "Production Bench 1"
  location: "Lab A"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    sim_config:
      voltage: 3.31
  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
    sim_config:
      voltage: 5.0
```

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Works with real hardware OR simulation."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    psu.set_voltage(5.0)
    psu.set_current_limit(1.0)
    psu.enable_output()

    voltage = dmm.measure_voltage()

    psu.disable_output()
    return voltage
```

**Run with hardware:**
```bash
pytest tests/ --station-config=stations/bench_1.yaml --dut-serial=SN12345
```

**Run simulated:**
```bash
pytest tests/ --station-config=stations/bench_1.yaml --simulate --dut-serial=SIM001
```

## What You Learned

- Station configuration with instruments
- The `instruments` fixture from station config
- `--simulate` flag for hardware-free testing
- Driver-level vs interface-level simulation
- VISA address formats

## Next Step

How does Litmus know which station can test which product?

[Step 8: Capability Matching →](08-capabilities.md)
