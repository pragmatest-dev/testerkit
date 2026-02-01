# pytest Plugin Guide

Litmus provides a pytest plugin for hardware testing with automatic measurement capture, limit checking, and result storage.

## Installation

The plugin is automatically registered when you install Litmus:

```bash
pip install -e .
# or
uv sync
```

Verify it's loaded:

```bash
pytest --co -q  # Should show litmus in plugins
```

## The @litmus_test Decorator

The `@litmus_test` decorator transforms a test function into a hardware test:

```python
from litmus.execution import litmus_test

@litmus_test
def test_voltage(context, dmm):
    """Measure and return voltage."""
    return dmm.measure_dc_voltage()
```

### What It Does

1. **Loads configuration** from `config.yaml` in the test directory
2. **Expands vectors** based on config (runs test multiple times if configured)
3. **Captures measurements** from return values
4. **Checks limits** against configured limits
5. **Records results** to the test run

### Return Values

**Single measurement:**
```python
@litmus_test
def test_voltage(context, dmm):
    return dmm.measure_dc_voltage()  # Stored as "test_voltage"
```

**Multiple measurements (dict):**
```python
@litmus_test
def test_power(context, dmm):
    return {
        "input_voltage": dmm.measure_dc_voltage(),
        "input_current": dmm.measure_dc_current(),
    }
```

**Streaming measurements (yield):**
```python
@litmus_test
def test_stability(context, dmm):
    for i in range(10):
        yield {"voltage": dmm.measure_dc_voltage()}
        time.sleep(1)
```

## The vector Fixture

Every `@litmus_test` function receives a `vector` parameter containing the current test parameters:

```python
@litmus_test
def test_sweep(context, psu, dmm):
    # Access vector parameters
    voltage = vector["voltage"]
    load = vector["load"]

    psu.set_voltage(voltage)
    return dmm.measure_dc_voltage()
```

### Vector Methods

**Access parameters:**
```python
vector["voltage"]      # Get parameter value
vector.get("temp", 25) # With default
vector.params          # All parameters as dict
vector.index           # 0-based index in expansion
```

**Change detection (for nested loops):**
```python
if vector.changed("temperature"):
    # Temperature changed since last vector
    set_chamber_temp(vector["temperature"])
```

## Test Configuration

Create `config.yaml` in your test directory:

```yaml
test_voltage:
  limits:
    test_voltage:
      low: 4.5
      high: 5.5
      units: V

test_sweep:
  vectors:
    expand: product
    voltage: [3.3, 5.0, 12.0]
    load: [0, 50, 100]
  limits:
    test_sweep:
      low: 3.0
      high: 13.0
      units: V

test_stability:
  vectors:
    - sample: 1
    - sample: 2
    - sample: 3
  retry:
    max_attempts: 3
    delay_seconds: 0.5
```

## Decorator Options

```python
@litmus_test(
    raise_on_fail=True,    # Raise exception if limit fails (default True)
    config_file="custom.yaml",  # Custom config file
)
def test_example(context, dmm):
    ...
```

### Characterization Mode

When no limits are configured, measurements are recorded as PASS (characterization):

```python
@litmus_test(raise_on_fail=False)
def test_characterize(context, dmm):
    return dmm.measure_dc_voltage()  # Always passes, records value
```

## Instrument Fixtures

Create pytest fixtures for your instruments:

```python
import pytest
from litmus.instruments import DMM, PSU

@pytest.fixture
def dmm():
    with DMM("TCPIP::192.168.1.100::INSTR") as d:
        yield d

@pytest.fixture
def psu():
    with PSU("GPIB0::5::INSTR") as p:
        yield p

# The instruments fixture from station config handles mock mode automatically
# Use --mock-instruments flag to enable mock mode
```

### Using the `pins` Fixture

For UUT-centric tests, use the `pins` fixture to access instruments via DUT pin names:

```python
def test_output_voltage(pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    voltage = pins["VOUT"].measure_voltage()
    assert float(voltage) > 3.0
```

### Mock Mode

Run all tests in mock mode (no hardware required):

```bash
pytest tests/ --station-config=stations/bench_1.yaml --mock-instruments --dut-serial=TEST001
```

Mock values come from station `mock_config` and can be overridden per-test with `_mock` in config.yaml.

## CLI Options

```bash
pytest tests/ \
  --dut-serial=SN12345 \       # Required: DUT serial number
  --station=bench_1 \          # Station ID (default: "default")
  --operator="Jane Doe" \      # Operator name
  --results-dir=./results \    # Results directory
  --test-phase=production \    # Test phase
  -v
```

## Results

Results are saved to Parquet files:

```
results/
├── test_runs/
│   └── 2026-01-28/
│       └── <run_id>.parquet
├── vectors/
│   └── 2026-01-28/
│       └── <run_id>_vectors.parquet
└── measurements/
    └── 2026-01-28/
        └── <run_id>_measurements.parquet
```

Query with the CLI:

```bash
litmus runs
litmus show <run_id>
```

Or programmatically:

```python
import pyarrow.parquet as pq

measurements = pq.read_table("results/measurements")
print(measurements.to_pandas())
```

## Complete Example

**tests/config.yaml:**
```yaml
test_input_voltage:
  limits:
    test_input_voltage:
      low: 4.5
      high: 5.5
      nominal: 5.0
      units: V
      spec_ref: PWR-IN-001

test_output_sweep:
  vectors:
    expand: product
    load_percent: [0, 50, 100]
  limits:
    test_output_sweep:
      low: 3.135
      high: 3.465
      units: V
  retry:
    max_attempts: 2
```

**tests/test_power.py:**
```python
import pytest
from litmus.execution import litmus_test
from litmus.instruments import DMM

@pytest.fixture
def dmm(instruments):
    return instruments["dmm"]

@litmus_test
def test_input_voltage(context, dmm):
    """Verify input voltage."""
    return dmm.measure_voltage()

@litmus_test
def test_output_sweep(context, dmm):
    """Sweep load conditions."""
    # vector["load_percent"] contains current load value
    return dmm.measure_voltage()
```

**Run:**
```bash
pytest tests/ --dut-serial=TEST001 -v
```

## Next Steps

- [Configuration Reference](configuration.md) — Detailed config options
- [Python Client](client.md) — Submit results from external tools
