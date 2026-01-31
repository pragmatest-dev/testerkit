# Step 5: Real Instruments

**Goal:** Connect to real hardware with the ability to simulate when unavailable.

## What You'll Build

A test that works with real instruments OR in simulation mode using the same code.

## Station Configuration

Define your test station:

```yaml
# stations/my_station.yaml
station:
  id: my_station
  name: "My Test Bench"
  location: "Lab A"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"

  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
```

This defines:
- Station identity and location
- A DMM at a TCP/IP address
- A PSU on GPIB

## Using Station Instruments

```python
# tests/test_power.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Test using station instruments."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    psu.set_voltage(5.0)
    psu.enable_output()

    return dmm.measure_voltage()
```

The `instruments` fixture provides access to station instruments by name.

## Running with Real Hardware

```bash
pytest tests/ --station=my_station --dut-serial=SN001
```

## Running in Simulation Mode

When hardware isn't available, use `--simulate`:

```bash
pytest tests/ --station=my_station --simulate --dut-serial=SN001
```

The same test code works in both modes!

## How Simulation Works

When `--simulate` is set:

1. Drivers use pyvisa-sim instead of real I/O
2. Responses come from `sim_config` values
3. No hardware required

Configure simulation values:

```yaml
# stations/my_station.yaml
instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    simulate: true          # Always simulate this instrument
    sim_config:
      voltage: 3.31
      current: 0.1
```

## The simulate Fixture

Access the simulation flag in tests:

```python
import pytest

@pytest.fixture
def dmm(simulate):
    """DMM fixture that respects --simulate flag."""
    from litmus.instruments import DMM

    with DMM(
        "TCPIP::192.168.1.100::INSTR",
        simulate=simulate,
        sim_config={"voltage": 3.31}
    ) as d:
        yield d
```

The `simulate` fixture is `True` when `--simulate` flag is passed.

## CI Configuration

Create a station for CI environments:

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
      voltage: 3.31

  psu:
    type: power_supply
    resource: "SIM::PSU"
    simulate: true
```

Run in CI:

```bash
pytest tests/ --station=ci_station --dut-serial=CI-TEST
```

## VISA Addresses

Common VISA address formats:

| Type | Format | Example |
|------|--------|---------|
| TCP/IP | `TCPIP::host::port::INSTR` | `TCPIP::192.168.1.100::INSTR` |
| GPIB | `GPIB0::address::INSTR` | `GPIB0::5::INSTR` |
| USB | `USB0::vid::pid::serial::INSTR` | `USB0::0x2A8D::0x0101::MY12345::INSTR` |
| Serial | `ASRL/dev/ttyUSB0::INSTR` | `ASRL/dev/ttyUSB0::INSTR` |

## Driver-Level vs Interface-Level Simulation

Litmus supports two simulation approaches:

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

**Station config:**
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

**Test code:**
```python
# tests/test_power.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, instruments):
    """Verify output voltage under load."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    # Apply input
    psu.set_voltage(5.0)
    psu.set_current_limit(1.0)
    psu.enable_output()

    # Measure output
    voltage = dmm.measure_voltage()

    # Cleanup
    psu.disable_output()

    return voltage
```

**Run with hardware:**
```bash
pytest tests/test_power.py --station=bench_1 --dut-serial=SN12345
```

**Run simulated:**
```bash
pytest tests/test_power.py --station=bench_1 --simulate --dut-serial=SIM001
```

## What You Learned

- How to configure stations with instruments
- Using the `instruments` fixture
- Running tests with real hardware vs simulation
- Different simulation approaches
- VISA address formats

## Next Step

How does Litmus know which station can test which product?

[Step 6: Capability Matching →](06-capabilities.md)
