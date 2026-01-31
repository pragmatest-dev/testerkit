# Simulation Mode

Develop and test without hardware using Litmus simulation features.

## Overview

Litmus supports two simulation levels:

| Level | Method | Use Case |
|-------|--------|----------|
| Driver-level | `simulate=True` | Integration tests, realistic timing |
| Interface-level | `MockDMM`, etc. | Unit tests, instant response |

## Driver-Level Simulation

### Per-Instrument

```python
from litmus.instruments import DMM

dmm = DMM(
    "TCPIP::192.168.1.100::INSTR",
    simulate=True,
    sim_config={"voltage": 3.31}
)
dmm.connect()
v = dmm.measure_voltage()  # Returns Decimal("3.31")
```

### Station Configuration

```yaml
# stations/bench_1.yaml
instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    simulate: true
    sim_config:
      voltage: 3.31
      current: 0.1
```

### Command-Line Flag

```bash
pytest tests/ --station=bench_1 --simulate --dut-serial=SIM001
```

The `--simulate` flag enables simulation for all instruments.

## Interface-Level Mocks

### Direct Use

```python
from litmus.instruments import MockDMM, MockPSU

# Create mock with initial values
dmm = MockDMM(voltage=3.31, current=0.1)
dmm.connect()

v = dmm.measure_voltage()  # Instant, returns Decimal("3.31")

# Update values
dmm.set_value("voltage", 5.0)
v = dmm.measure_voltage()  # Returns Decimal("5.0")
```

### Available Mocks

| Mock | Simulates |
|------|-----------|
| `MockDMM` | Digital multimeter |
| `MockPSU` | Power supply |
| `MockELoad` | Electronic load |
| `MockScope` | Oscilloscope |
| `MockFuncGen` | Function generator |

### In Tests

```python
import pytest
from litmus.instruments import MockDMM

@pytest.fixture
def dmm():
    """Mock DMM for testing."""
    with MockDMM(voltage=3.31) as d:
        yield d

def test_voltage(dmm):
    v = dmm.measure_voltage()
    assert float(v) > 3.0
```

## Simulation Patterns

### Environment-Based

```python
import os
from litmus.instruments import DMM

simulate = os.environ.get("LITMUS_SIMULATE", "").lower() == "true"

with DMM("TCPIP::192.168.1.100::INSTR", simulate=simulate) as dmm:
    voltage = dmm.measure_voltage()
```

### pytest Fixture

```python
@pytest.fixture
def simulate(request):
    """Get simulation mode from command line."""
    return request.config.getoption("--simulate", False)

@pytest.fixture
def dmm(simulate):
    """DMM that respects --simulate flag."""
    with DMM(
        "TCPIP::192.168.1.100::INSTR",
        simulate=simulate,
        sim_config={"voltage": 3.31}
    ) as d:
        yield d
```

### CI Station

```yaml
# stations/ci_station.yaml
station:
  id: ci_station
  name: "CI Environment"

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

```bash
# In CI pipeline
pytest tests/ --station=ci_station --dut-serial=CI-TEST
```

## Configuring Simulation Values

### Static Values

```yaml
sim_config:
  voltage: 3.31
  current: 0.1
  resistance: 1000
```

### Dynamic Updates

```python
from litmus.instruments import MockDMM

dmm = MockDMM(voltage=3.31)
dmm.connect()

# Initial value
v1 = dmm.measure_voltage()  # 3.31

# Change simulated value
dmm.set_value("voltage", 5.0)
v2 = dmm.measure_voltage()  # 5.0
```

### Behavior Simulation

For more complex simulation, extend the mock:

```python
from decimal import Decimal
from litmus.instruments import MockDMM

class RealisticMockDMM(MockDMM):
    """Mock DMM with noise and drift."""

    def measure_voltage(self):
        import random
        base = float(self._values["voltage"])
        noise = random.gauss(0, 0.01)  # 10mV noise
        return Decimal(str(round(base + noise, 4)))
```

## Testing Both Modes

### Parametrized Tests

```python
import pytest
from litmus.instruments import DMM, MockDMM

@pytest.fixture(params=["mock", "simulate"])
def dmm(request):
    if request.param == "mock":
        with MockDMM(voltage=3.31) as d:
            yield d
    else:
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.31}
        ) as d:
            yield d

def test_measure_voltage(dmm):
    """Test works with both mock and simulated driver."""
    v = dmm.measure_voltage()
    assert float(v) == pytest.approx(3.31, abs=0.1)
```

### Hardware Tests

```python
import pytest

@pytest.mark.hardware
def test_real_measurement():
    """Test requiring real hardware."""
    from litmus.instruments import DMM

    with DMM("TCPIP::192.168.1.100::INSTR") as dmm:
        v = dmm.measure_voltage()
        assert isinstance(v, Decimal)
```

Run hardware tests separately:

```bash
pytest -m hardware           # Only hardware tests
pytest -m "not hardware"     # Skip hardware tests
```

## Best Practices

### 1. Default to Simulation

Make tests run simulated by default:

```python
# conftest.py
def pytest_addoption(parser):
    parser.addoption(
        "--hardware",
        action="store_true",
        help="Run with real hardware (default: simulate)"
    )

@pytest.fixture
def simulate(request):
    return not request.config.getoption("--hardware")
```

### 2. Realistic Values

Use values close to real measurements:

```yaml
# Good: realistic values
sim_config:
  voltage: 3.31
  current: 0.102

# Bad: obviously fake
sim_config:
  voltage: 1234
  current: 5678
```

### 3. Test Edge Cases

Simulate failure conditions:

```python
def test_out_of_range():
    dmm = MockDMM(voltage=99.99)  # Way out of spec
    dmm.connect()
    v = dmm.measure_voltage()
    # Test handles out-of-range values correctly
```

### 4. CI Configuration

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --station=ci_station \
      --dut-serial=CI-TEST \
      -v
```

## Comparison

| Feature | `simulate=True` | `MockDMM` |
|---------|----------------|-----------|
| I/O through pyvisa-sim | Yes | No |
| Realistic timing | Yes | No |
| Tests driver code | Yes | No |
| Speed | ~5-50ms/call | Instant |
| Dependencies | pyvisa-sim | None |
| Use case | Integration | Unit tests |

## When to Use Each

### Use `simulate=True` when:
- Testing driver communication logic
- Need realistic timing behavior
- Testing error handling
- Integration testing

### Use `MockDMM` when:
- Unit testing business logic
- CI/CD pipelines need speed
- No pyvisa dependency wanted
- Testing in isolation

## Next Steps

- [Writing Tests](writing-tests.md) — Test patterns
- [Configuring Stations](configuring-stations.md) — Station configuration
- [Adding Instruments](adding-instruments.md) — Custom drivers with simulation
