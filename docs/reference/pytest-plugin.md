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

## The context Fixture

Every `@litmus_test` function receives a `context` parameter containing the current test parameters:

```python
@litmus_test
def test_sweep(context, psu, dmm):
    # Access context parameters
    voltage = context.inputs["voltage"]
    load = context.get_in("load", 0.1)  # With default

    psu.set_voltage(voltage)
    return dmm.measure_dc_voltage()
```

### Context Methods

**Access parameters:**
```python
context.inputs["voltage"]     # Get parameter value from merged dict
context.get_in("temp", 25)    # Get with default (checks parent chain)
context.inputs                # All inputs as dict (includes inherited)
context.inputs.get("_index")  # 0-based index in expansion
```

**Change detection (for nested loops):**
```python
if context.changed("temperature"):
    # Temperature changed since last context
    set_chamber_temp(context.inputs["temperature"])
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

## @litmus_test Parameters

All parameters are optional:

```python
@litmus_test(
    config=None,           # Inline config dict (highest precedence)
    config_file=None,      # Path to YAML config (relative to test file)
    retry=None,            # RetryConfig override
    limits=None,           # Dict of limit overrides by measurement name
    raise_on_fail=True,    # Raise AssertionError if limit fails
)
def test_example(context, dmm):
    ...
```

### Parameter Details

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `dict` | `None` | Inline configuration with vectors, limits, retry |
| `config_file` | `str` | `None` | Path to YAML config file (relative to test file) |
| `retry` | `RetryConfig` | `None` | Override retry config from file |
| `limits` | `dict` | `None` | Override limits by measurement name |
| `raise_on_fail` | `bool` | `True` | Raise on failed measurements |

### Config Resolution Order

1. **Inline parameters** (highest) - `config=`, `retry=`, `limits=`
2. **Explicit config_file** - `config_file="custom.yaml"`
3. **Auto-discovered config.yaml** (lowest) - In same directory as test file

### Examples

**Inline config (no YAML needed):**
```python
@litmus_test(
    config={
        "vectors": {"expand": "product", "vin": [5.0, 12.0], "load": [0.1, 0.5]},
        "limits": {"test_sweep": {"low": 3.0, "high": 13.0, "units": "V"}},
    }
)
def test_sweep(context, dmm):
    return dmm.measure_dc_voltage()
```

**Override limits only:**
```python
from litmus.config.models import Limit

@litmus_test(
    limits={
        "voltage": Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
    }
)
def test_voltage(context, dmm):
    return {"voltage": dmm.measure_dc_voltage()}
```

**Override retry only:**
```python
from litmus.config.models import RetryConfig

@litmus_test(
    retry=RetryConfig(max_attempts=5, delay_seconds=1.0)
)
def test_flaky(context, dmm):
    return dmm.measure_dc_voltage()
```

**Custom config file:**
```python
@litmus_test(config_file="special_config.yaml")
def test_special(context, dmm):
    return dmm.measure_dc_voltage()
```

**Characterization mode (don't fail on limits):**
```python
@litmus_test(raise_on_fail=False)
def test_characterize(context, dmm):
    # Measurements recorded but won't fail test
    return dmm.measure_dc_voltage()
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

# For mock mode, use --mock-instruments with station config
# Mock values come from station mock_config or test _mock config
```

### Station-Based Fixtures

When using `--station-config`, the plugin provides automatic instrument management:

**`instruments` fixture (session-scoped):**
```python
@litmus_test
def test_voltage(context, instruments):
    dmm = instruments["dmm"]       # Access by station config name
    psu = instruments["psu"]
    psu.set_voltage(5.0)
    return dmm.measure_dc_voltage()
```

**`pins` fixture (session-scoped):**

For UUT-centric tests, access instruments via DUT pin names:

```python
def test_output_voltage(pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    voltage = pins["VOUT"].measure_voltage()
    assert float(voltage) > 3.0
```

Requires `--fixture-config` to map pins to instruments.

**`fixture_manager` fixture (session-scoped):**

For advanced routing needs:

```python
def test_with_net_lookup(fixture_manager):
    point = fixture_manager.get_point_for_net("VOUT_3V3")
    instrument = fixture_manager.get_instrument_for_point(point.name)
```

**`spec_context` fixture (session-scoped):**

For spec-driven limit derivation:

```python
def test_voltage(spec_context, dmm):
    # Get limit from product spec with guardband
    limit = spec_context.get_limit("output_voltage", temperature=25)
    value = dmm.measure_dc_voltage()
    # Framework checks against derived limit
```

Auto-discovers from `products/` directory or use `--spec` option.

**`run_context` fixture (session-scoped):**

Add custom metadata that becomes queryable Parquet columns:

```python
def test_with_metadata(run_context, psu, dmm):
    # These become columns in the Parquet file
    run_context.set("operator_badge", "EMP-12345")
    run_context.set("operator_shift", "day")
    run_context.set("fixture_serial", "FIX-001")
    run_context.set("ambient_temp", 23.5)

    # Normal test code...
    psu.set_voltage(5.0)
    return dmm.measure_dc_voltage()
```

Custom fields are denormalized onto every measurement row for easy querying.

**Hierarchical Context:**

The TestHarness provides hierarchical context with scoped inheritance:

- **Run level** (`harness.run_context`): Data visible to all steps and vectors
- **Step level** (`harness.context` inside step): Data visible to all vectors in that step
- **Vector level** (`harness.context` inside run_vector): Data visible only to that vector

Data set at parent level is inherited by children:

```python
from litmus.execution.harness import TestHarness

harness = TestHarness(step_name="my_test")

# Run-level context persists across all steps
harness.run_context.configure("operator", "jane")

with harness.step():
    # Step-level context
    harness.context.configure("fixture.id", "FIX-01")

    with harness.run_vector(vector) as tv:
        # Vector context inherits from step and run
        harness.context.observe("temp_probe.temp", 24.8)

        # tv.params includes: operator, fixture.id, temp
```

**`litmus_logger` fixture (session-scoped, autouse):**

The underlying logger that captures all measurements. Automatically active for all tests. You rarely need to access it directly—use `run_context` for custom metadata or `@litmus_test` for measurement capture.

**`mock_instruments` fixture (session-scoped):**

Returns `True` if `--mock-instruments` flag or `LITMUS_MOCK_INSTRUMENTS=1` environment variable is set:

```python
@pytest.fixture
def dmm(mock_instruments):
    if mock_instruments:
        from litmus.instruments import Mock
        with Mock(DMM, voltage=5.0) as d:
            yield d
    else:
        with DMM("TCPIP::192.168.1.100::INSTR") as d:
            yield d
```

### CLI Options for Mock Mode

Run all tests with mock instruments:

```bash
pytest tests/ --mock-instruments --dut-serial=TEST001
```

## Test Phase

The `test_phase` field categorizes test runs (e.g., `development`, `validation`, `characterization`, `production`). It's recorded in the Parquet output for filtering results.

### Setting Test Phase

You can set test phase in multiple ways (priority order):

1. **CLI option:** `--test-phase=validation`
2. **Environment variable:** `LITMUS_TEST_PHASE=validation`
3. **Sequence YAML:** `test_phase: validation` (when run via UI/runner)
4. **Auto-detect:** `production` if git clean, `development` if dirty

```bash
# Request validation phase
pytest tests/ --test-phase=validation

# Via environment variable
LITMUS_TEST_PHASE=characterization pytest tests/

# Auto-detect (default) - production if clean, development if dirty
pytest tests/
```

**Sequence YAML example:**
```yaml
sequence:
  id: power_board_validation
  name: "Power Board Validation"
  test_phase: validation  # Applied when run through UI/runner

steps:
  - name: power_on
    test: test_power.test_power_on
```

### Git Status Enforcement

**Important:** Non-development phases require a clean git repository. If git is unavailable or there are uncommitted changes, the phase is **always** `development` regardless of what's requested.

| Git Status | Requested Phase | Actual Phase |
|------------|-----------------|--------------|
| Clean | `validation` | `validation` |
| Clean | `production` | `production` |
| Clean | (none) | `production` |
| Dirty | `validation` | `development` |
| Dirty | `production` | `development` |
| Dirty | (none) | `development` |
| No git | (any) | `development` |

This ensures non-development runs can only be created from committed, reproducible code.

### Query by Phase

```python
import duckdb

# Only production runs
duckdb.sql("""
    SELECT * FROM read_parquet('results/runs/**/*.parquet')
    WHERE test_phase = 'production'
""")

# Exclude development runs
duckdb.sql("""
    SELECT * FROM read_parquet('results/runs/**/*.parquet')
    WHERE test_phase != 'development'
""")
```

## CLI Options

```bash
pytest tests/ \
  --dut-serial=SN12345 \         # DUT serial number (default: DUT001)
  --station=bench_1 \            # Station ID (default: station_001)
  --operator="Jane Doe" \        # Operator name
  --results-dir=./results \      # Results directory (default: results)
  --mock-instruments \           # Use mock instruments instead of real hardware
  --spec=products/x/spec.yaml \  # Path to product spec YAML
  --guardband=10 \               # Default guardband percentage (default: 0)
  --station-config=stations/bench_1.yaml \  # Station config file
  --fixture-config=fixtures/x.yaml \        # Fixture config file
  --test-phase=validation \      # Test phase (default: auto-detect from git)
  -v
```

| Option | Default | Description |
|--------|---------|-------------|
| `--dut-serial` | `DUT001` | DUT serial number |
| `--station` | `station_001` | Station ID |
| `--operator` | `None` | Operator name |
| `--results-dir` | `results` | Directory for Parquet results |
| `--mock-instruments` | `False` | Use mock instruments instead of real hardware |
| `--spec` | `None` | Path to product spec YAML file |
| `--guardband` | `0` | Default guardband percentage |
| `--station-config` | `None` | Path to station configuration YAML |
| `--fixture-config` | `None` | Path to fixture configuration YAML |
| `--test-phase` | auto | Test phase (development, validation, characterization, production) |

## Markers

### `@pytest.mark.litmus_retry`

Retry failed tests automatically:

```python
import pytest

@pytest.mark.litmus_retry(max_attempts=3, delay=0.5)
@litmus_test
def test_flaky_measurement(context, dmm):
    return dmm.measure_dc_voltage()
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_attempts` | `3` | Maximum retry attempts |
| `delay` | `0.0` | Delay in seconds between retries |

### `@pytest.mark.litmus_skip_on`

Skip test if dependencies failed:

```python
import pytest

@litmus_test
def test_power_on(context, psu):
    psu.enable_output()
    return psu.measure_voltage()

@pytest.mark.litmus_skip_on(["test_power_on"])
@litmus_test
def test_output_voltage(context, dmm):
    # Skipped if test_power_on failed
    return dmm.measure_dc_voltage()
```

Dependencies can be test function names or full node IDs.

## Results

Results are saved to Parquet files with **one row per measurement** and all metadata denormalized:

```
results/runs/{date}/{timestamp}_{serial}.parquet   # With serial
results/runs/{date}/{timestamp}.parquet            # Without serial (dev/debug)
```

**Key principles:**
- UTC timestamps for consistent cross-timezone analysis
- Self-describing filename (timestamp + serial)
- Chronological sorting in file listings
- Portable (copy the file anywhere and you know what it is)

### Streaming Journal

During test execution, measurements are streamed to a JSONL journal file for:
- **Live observability** — UI updates in real-time as measurements are captured
- **Crash recovery** — No data lost if test is interrupted (power failure, crash, abort)

```
results/.journals/{date}/{timestamp}_{serial}/
├── measurements.jsonl     # One line per measurement (streamed)
└── _ref/                  # Large data files (waveforms, images)
```

On successful completion, the journal is converted to Parquet and deleted. If a test crashes, the journal survives and can be recovered:

```bash
litmus journals              # List orphaned journals
litmus recover <journal>     # Convert journal to parquet
litmus recover --all         # Recover all orphaned journals
```

Query with the CLI:

```bash
litmus runs
litmus show <run_id>
```

Or programmatically:

```python
import pandas as pd

# Load a specific run
df = pd.read_parquet("results/runs/2026-01-28/20260128T143025Z_SN001.parquet")

# Filter to specific test
vout = df[df["step_name"] == "test_output_voltage"]

# Analyze by input condition
print(vout.groupby("in_vin")["value"].mean())
```

For cross-run analysis:

```python
import duckdb

# Query all runs
duckdb.sql("""
    SELECT measurement_name, AVG(value), COUNT(*)
    FROM read_parquet('results/runs/**/*.parquet')
    GROUP BY measurement_name
""")
```

See [Parquet Schema Reference](parquet-schema.md) for the complete column list.

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
    # context.inputs["load_percent"] contains current load value
    return dmm.measure_voltage()
```

**Run:**
```bash
pytest tests/ --dut-serial=TEST001 -v
```

## Next Steps

- [Configuration Reference](configuration.md) — Detailed config options
- [Python Client](client.md) — Submit results from external tools
