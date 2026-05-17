# Adding Litmus to Existing pytest Projects

Integrate Litmus into an existing pytest test suite without rewriting your tests.

> **New to Litmus?** pytest-native mode (plain tests + `context`/`verify`/`logger` fixtures) is
> the default. See [pytest-native Reference](../reference/pytest-native.md) for the full
> authoring contract. This page focuses on mixing Litmus into suites that already exist.

## Overview

If you already have pytest tests, you can:
1. Keep existing tests as-is
2. Add Litmus features incrementally
3. Mix Litmus and non-Litmus tests

## Quick Integration

### Step 1: Install Litmus

```bash
# Install from source (not yet on PyPI)
git clone https://github.com/pragmatest-dev/litmus.git && cd litmus && uv sync
```

### Step 2: Add to conftest.py

```python
# tests/conftest.py

def pytest_addoption(parser):
    """Add Litmus command-line options."""
    parser.addoption("--dut-serial", action="store", help="DUT serial number")
    parser.addoption("--station", action="store", default="default", help="Station ID")
    parser.addoption("--mock-instruments", action="store_true", help="Simulate instruments")
```

### Step 3: Use Litmus in New Tests

```python
# tests/test_new_feature.py
def test_new_voltage_check(context, dmm, logger):
    """New test using Litmus."""
    logger.measure("voltage", dmm.measure_voltage())
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
from litmus.client import LitmusClient

@pytest.fixture(scope="session")
def litmus_client():
    return LitmusClient()

@pytest.fixture
def litmus_run(litmus_client, request):
    """Create a Litmus run for each test."""
    run = litmus_client.start_run(
        dut_serial=request.config.getoption("--dut-serial") or "UNKNOWN",
        station_id=request.config.getoption("--station") or "default",
        test_phase="production",
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

For more detailed tracking, wrap the measurement block in a harness step (`harness.step(name)` is a context manager; there is no `harness.finish()` method):

```python
from litmus.execution.harness import TestHarness
from litmus.execution.logger import TestRunLogger

logger = TestRunLogger(dut_serial="SN001", station_id="bench_1")

def test_power_rails():
    """Existing test with a Litmus harness step."""
    harness = TestHarness(logger=logger)

    with harness.step("test_power_rails"):
        vcc = measure_vcc()
        vdd = measure_vdd()

        harness.measure("vcc", vcc, units="V", low=3.2, high=3.4)
        harness.measure("vdd", vdd, units="V", low=1.7, high=1.9)

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

# After — bring your own driver class; Litmus provides the VISA base
from litmus.instruments.visa import VisaInstrument

class MyDMM(VisaInstrument):
    def measure_voltage(self) -> float:
        return float(self.query("MEAS:VOLT:DC?"))

def measure_voltage(simulate=False):
    with MyDMM("TCPIP::192.168.1.100::INSTR", simulate=simulate) as dmm:
        return dmm.measure_voltage()
```

### Level 4: Full pytest-native

Convert tests to use Litmus's per-test fixtures (`context`, `verify`, `logger` are the common entry points — see [Litmus fixtures](../reference/litmus-fixtures.md) for the full 20-fixture surface):

```python
def test_voltage(dmm, logger):
    """Fully converted Litmus test."""
    logger.measure("voltage", dmm.measure_voltage())
```

## Fixture Patterns

### Using Station Instruments

The recommended approach is to use the `instruments` fixture from station config:

```python
# tests/conftest.py
import pytest

@pytest.fixture(scope="session")
def dmm(instruments):
    """DMM from station config."""
    return instruments["dmm"]

@pytest.fixture(scope="session")
def psu(instruments):
    """PSU from station config."""
    return instruments["psu"]
```

Run with `--mock-instruments` for hardware-free testing:

```bash
pytest tests/ --station=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

### Station-Based Fixtures

The Litmus pytest plugin already exposes a session-scoped `station` and
per-test `instruments` fixture once `--station=<id>` is passed. If you
need to load the YAML directly (e.g. in non-pytest code), use the store:

```python
from litmus.store import load_station

# Returns a validated StationConfig
station = load_station("bench_1")
for name, cfg in station.instruments.items():
    print(name, cfg.driver, cfg.resource, cfg.mock)
```

Each `StationInstrumentConfig` carries `driver`, `resource`, and a
`mock: bool` flag. Instantiating the drivers is the runner's job —
the bundled pytest plugin's `instruments` fixture handles it for you;
custom runners construct the driver class with `simulate=cfg.mock`.

## Configuration Files

### Project Config

```yaml
# litmus.yaml — flat ProjectConfig (no project: / defaults: wrappers)
name: "My Existing Project"
data_dir: "results"
default_station: "bench_1"
mock_instruments: false
```

### Test Config

```yaml
# tests/test_<module>.yaml
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
pytest tests/ --mock-instruments -v

# Run specific test
pytest tests/test_voltage.py -v
```

### CI/CD

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --mock-instruments \
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
def test_with_litmus(dmm, logger):
    logger.measure("voltage", dmm.measure_voltage())


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
- [Litmus fixtures](../reference/litmus-fixtures.md) — all 20 plugin fixtures
- [Litmus markers](../reference/litmus-markers.md) — the seven `litmus_*` markers
- [pytest-native Reference](../reference/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
