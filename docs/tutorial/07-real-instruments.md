# Step 7: Real Instruments

**Goal:** Connect to real hardware with the ability to mock when unavailable.

## What You'll Build

A test that works with real instruments OR in mock mode using the same code.

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
    mock_config:
      voltage: 3.31       # Value returned in mock mode
      current: 0.1

  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
      current: 0.1
```

This defines:
- A station identity and location
- A DMM at a TCP/IP address with mock values
- A PSU on GPIB with mock values

## The instruments Fixture

When you run with `--station-config`, Litmus provides an `instruments` fixture:

```python
# tests/test_power.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, instruments):
    """Access instruments by name from station config."""
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    psu.set_voltage(5.0)
    psu.enable_output()

    return dmm.measure_voltage()
```

Run with real hardware:
```bash
pytest tests/ --station-config=stations/bench_1.yaml --dut-serial=SN001
```

## Running with Mock Instruments

When hardware isn't available, add `--mock-instruments`:

```bash
pytest tests/ --station-config=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

The **same test code** works in both modes.

## How Mock Mode Works

When `--mock-instruments` is set:
1. Mock instruments are used instead of real drivers
2. Responses come from `mock_config` values in station config
3. No hardware required

## Per-Test Mock Values

For tests that need specific mock values, use `mocks` in the sequence step:

```yaml
# sequences/my_sequence.yaml
steps:
  - id: output_voltage
    test: tests/test_voltage.py::test_output_voltage
    mocks:
      dmm.measure_voltage: 3.31
      psu.measure_current: 0.5
    limits:
      test_output_voltage:
        low: 3.2
        high: 3.4
        units: V
```

## Mock Value Priority

When running with `--mock-instruments`, values are resolved in order:

1. **Vector-level `_mocks`** — Specific to this test vector
2. **Step-level `mocks`** — Constant for all vectors in this step
3. **Station `mock_config`** — Default for this instrument
4. **Zero** — If nothing else configured

## CI/CD Configuration

In CI, always run with `--mock-instruments`:

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --station-config=stations/bench_1.yaml \
      --mock-instruments \
      --dut-serial=CI-TEST \
      -v
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
    mock_config:
      voltage: 3.31
  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
```

**sequences/smoke.yaml:**
```yaml
sequence:
  id: smoke
  name: "Smoke Test"

steps:
  - id: output_voltage
    test: tests/test_power.py::test_output_voltage
    limits:
      test_output_voltage:
        low: 3.135
        high: 3.465
        nominal: 3.3
        units: V
    mocks:
      dmm.measure_voltage: 3.31
```

**tests/test_power.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, instruments):
    """Works with real hardware OR mock mode."""
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

**Run with mocks:**
```bash
pytest tests/ --station-config=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

## What You Learned

- Station configuration with instruments and `mock_config`
- The `instruments` fixture from station config
- `--mock-instruments` flag for hardware-free testing
- Per-test mock values with `mocks` in sequence steps
- VISA address formats

## Next Step

How does Litmus know which station can test which product?

[Step 8: Capability Matching →](08-capabilities.md)
