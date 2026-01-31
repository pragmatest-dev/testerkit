# Adding Litmus to Existing pytest Projects

Integrate Litmus into an existing pytest test suite without rewriting your tests.

## Overview

If you already have pytest tests, you can:
1. Keep existing tests as-is
2. Add Litmus features incrementally
3. Mix Litmus and non-Litmus tests

## Quick Integration

### Step 1: Install Litmus

```bash
pip install litmus  # or add to requirements.txt
```

### Step 2: Add to conftest.py

```python
# tests/conftest.py

def pytest_addoption(parser):
    """Add Litmus command-line options."""
    parser.addoption("--dut-serial", action="store", help="DUT serial number")
    parser.addoption("--station", action="store", default="default", help="Station ID")
    parser.addoption("--simulate", action="store_true", help="Simulate instruments")
```

### Step 3: Use Litmus in New Tests

```python
# tests/test_new_feature.py
from litmus.execution import litmus_test

@litmus_test
def test_new_voltage_check(vector, instruments):
    """New test using Litmus."""
    return instruments["dmm"].measure_voltage()
```

### Step 4: Keep Existing Tests

```python
# tests/test_existing.py
def test_existing_feature():
    """Existing test - no changes needed."""
    assert calculate_something() == expected_value
```

Both test types run together:

```bash
pytest tests/ --dut-serial=SN12345 --station=bench_1
```

## Incremental Adoption

### Level 1: Just Results

Add result tracking without changing test logic:

```python
# tests/conftest.py
import pytest
from litmus import LitmusClient

@pytest.fixture(scope="session")
def litmus_client():
    return LitmusClient()

@pytest.fixture
def litmus_run(litmus_client, request):
    """Create a Litmus run for each test."""
    run = litmus_client.start_run(
        dut_serial=request.config.getoption("--dut-serial") or "UNKNOWN",
        station_id=request.config.getoption("--station") or "default",
        test_sequence_id="pytest_suite",
    )
    yield run
    run.finish()
```

Use in tests that need tracking:

```python
def test_voltage(litmus_run):
    """Existing test with Litmus result tracking."""
    voltage = measure_voltage()  # Your existing code

    with litmus_run.step("voltage_check") as step:
        step.measure("voltage", voltage, units="V", low=3.0, high=3.6)

    assert 3.0 < voltage < 3.6  # Keep existing assert
```

### Level 2: Add TestHarness

For more detailed tracking:

```python
from litmus.execution.harness import TestHarness

def test_power_rails():
    """Existing test with Litmus harness."""
    harness = TestHarness("test_power_rails")

    # Your existing measurement code
    vcc = measure_vcc()
    vdd = measure_vdd()

    harness.measure("vcc", vcc, units="V", low=3.2, high=3.4)
    harness.measure("vdd", vdd, units="V", low=1.7, high=1.9)

    harness.finish()

    # Keep existing asserts if desired
    assert vcc > 3.2
    assert vdd > 1.7
```

### Level 3: Use Litmus Instruments

Replace custom instrument code with Litmus drivers:

```python
# Before
def measure_voltage():
    import visa
    rm = visa.ResourceManager()
    dmm = rm.open_resource("TCPIP::192.168.1.100::INSTR")
    voltage = float(dmm.query("MEAS:VOLT:DC?"))
    dmm.close()
    return voltage

# After
from litmus.instruments import DMM

def measure_voltage(simulate=False):
    with DMM("TCPIP::192.168.1.100::INSTR", simulate=simulate) as dmm:
        return float(dmm.measure_voltage())
```

### Level 4: Full @litmus_test

Convert tests to use the decorator:

```python
from litmus.execution import litmus_test

@litmus_test
def test_voltage(vector, instruments):
    """Fully converted Litmus test."""
    return instruments["dmm"].measure_voltage()
```

## Fixture Patterns

### Shared Instruments

```python
# tests/conftest.py
import pytest
from litmus.instruments import DMM, PSU

@pytest.fixture(scope="session")
def dmm(request):
    """DMM shared across all tests."""
    simulate = request.config.getoption("--simulate")
    with DMM(
        "TCPIP::192.168.1.100::INSTR",
        simulate=simulate,
        sim_config={"voltage": 3.31}
    ) as d:
        yield d

@pytest.fixture(scope="session")
def psu(request):
    """PSU shared across all tests."""
    simulate = request.config.getoption("--simulate")
    with PSU(
        "GPIB0::5::INSTR",
        simulate=simulate
    ) as p:
        yield p
```

### Station-Based Fixtures

```python
# tests/conftest.py
import pytest
from litmus.config.loader import load_station

@pytest.fixture(scope="session")
def station(request):
    """Load station from config."""
    station_id = request.config.getoption("--station")
    return load_station(f"stations/{station_id}.yaml")

@pytest.fixture
def instruments(station, request):
    """Get instruments from station."""
    simulate = request.config.getoption("--simulate")
    return station.get_instruments(simulate=simulate)
```

## Configuration Files

### Project Config

```yaml
# litmus.yaml
project:
  name: "My Existing Project"
  results_dir: "results"

defaults:
  station: "bench_1"
  test_phase: "development"
```

### Test Config

```yaml
# tests/config.yaml
test_voltage:
  limits:
    voltage:
      low: 3.0
      high: 3.6
      units: V

test_power_rails:
  limits:
    vcc:
      low: 3.2
      high: 3.4
      units: V
    vdd:
      low: 1.7
      high: 1.9
      units: V
```

## Running Tests

### Development

```bash
# Run all tests (Litmus and non-Litmus)
pytest tests/ -v

# Run with simulation
pytest tests/ --simulate -v

# Run specific test
pytest tests/test_voltage.py -v
```

### CI/CD

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --simulate \
      --dut-serial=CI-TEST \
      --station=ci_station \
      -v
```

### Production

```bash
pytest tests/ \
  --station=bench_1 \
  --dut-serial=$SERIAL \
  -v
```

## Coexistence Tips

### Mark Tests

Use pytest markers to distinguish test types:

```python
import pytest

@pytest.mark.litmus
@litmus_test
def test_with_litmus(vector):
    ...

@pytest.mark.unit
def test_without_litmus():
    ...
```

Run specific types:

```bash
pytest -m litmus      # Only Litmus tests
pytest -m "not litmus"  # Only non-Litmus tests
```

### Separate Directories

```
tests/
├── unit/           # Non-Litmus unit tests
│   └── test_*.py
├── integration/    # Litmus integration tests
│   └── test_*.py
└── conftest.py     # Shared configuration
```

### Gradual Migration

1. Start with new tests using Litmus
2. Convert high-value tests first
3. Keep low-value tests as-is
4. Use results bridge for legacy tests

## Benefits of Integration

- **No big-bang migration** — Adopt incrementally
- **Keep existing tests** — They continue to work
- **Shared fixtures** — Litmus and pytest fixtures coexist
- **Unified reporting** — All results in one place
- **CI compatible** — Works with existing pipelines

## Next Steps

- [Test Harness](harness.md) — Add tracking to existing tests
- [Instrument Drivers](instruments.md) — Replace custom instrument code
- [pytest Plugin](../reference/pytest-plugin.md) — Full plugin reference
