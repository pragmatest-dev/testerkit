# pytest Plugin Guide

Litmus provides a pytest plugin for hardware testing with automatic measurement capture, limit checking, and result storage.

> **New in this release:** the pytest-native three-object split
> (`context` / `spec` / `logger`) is the preferred authoring style for new
> tests. See [pytest-native reference](pytest-native.md) for the
> `LitmusSequence` base class, unified sidecar YAML, and limit resolution
> chain. The `@litmus_test` decorator described below still works and is
> not deprecated.

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
    voltage = context.params["voltage"]
    load = context.get_param("load", 0.1)  # With default

    psu.set_voltage(voltage)
    return dmm.measure_dc_voltage()
```

### Context Methods

**Access parameters:**
```python
context.params["voltage"]     # Get parameter value from merged dict
context.get_param("temp", 25)    # Get with default (checks parent chain)
context.params                # All inputs as dict (includes inherited)
context.params.get("_index")  # 0-based index in expansion
```

**Change detection (for nested loops):**
```python
if context.changed("temperature"):
    # Temperature changed since last context
    set_chamber_temp(context.params["temperature"])
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

### Auto-Registered Role Fixtures

When a station config is loaded, the Litmus plugin **automatically registers a session-scoped fixture for each instrument role**. If your station config defines `dmm`, `psu`, `eload`, and `scope`, you can use them directly in tests with zero conftest boilerplate:

```python
@litmus_test
def test_voltage(context, dmm, psu):
    """dmm and psu are auto-registered from station config."""
    psu.set_voltage(5.0)
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

No `conftest.py` fixture definitions needed -- the plugin reads your station config at startup and creates the fixtures for you.

**Override behavior:** To customize an auto-registered fixture (e.g. add setup/teardown), define a fixture with the same name in your `conftest.py`. Standard pytest override rules apply -- conftest fixtures take precedence over plugin fixtures:

```python
# conftest.py
@pytest.fixture(scope="session")
def psu(instruments):
    """Custom PSU with default voltage."""
    inst = instruments.get("psu")
    inst.set_voltage(5.0)
    return inst
```

### `instrument` Accessor Fixture

For programmatic access with grouping support, use the `instrument` fixture:

```python
def test_voltage(instrument):
    dmm = instrument("dmm")       # Get by role name
    voltage = dmm.measure_dc_voltage()

def test_all_dmms(instrument):
    # Get all instruments with a specific driver class
    dmms = instrument.by_type("pymeasure.instruments.keithley.Keithley2000")
    for role, dmm in dmms.items():
        print(f"{role}: {dmm.measure_dc_voltage()}")

def test_list_roles(instrument):
    roles = instrument.roles()     # ["dmm", "eload", "psu", "scope"]
```

| Method | Returns | Description |
|--------|---------|-------------|
| `instrument(role)` | instrument instance | Get by role name, KeyError if missing |
| `instrument.by_type(driver_path)` | `dict[str, Any]` | All instruments matching a driver import path |
| `instrument.roles()` | `list[str]` | Sorted list of available role names |

### `instruments` Dict Fixture (session-scoped)

The underlying dict of all instrument instances, keyed by role name:

```python
@litmus_test
def test_voltage(context, instruments):
    dmm = instruments["dmm"]
    psu = instruments["psu"]
    psu.set_voltage(5.0)
    return dmm.measure_dc_voltage()
```

### `pins` Fixture (session-scoped)

For UUT-centric tests, access instruments via DUT pin names:

```python
def test_output_voltage(pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    voltage = pins["VOUT"].measure_voltage()
    assert float(voltage) > 3.0
```

Requires `--fixture-config` to map pins to instruments.

### `fixture_manager` Fixture (session-scoped)

For advanced routing needs:

```python
def test_with_net_lookup(fixture_manager):
    point = fixture_manager.get_point_for_net("VOUT_3V3")
    instrument = fixture_manager.get_instrument_for_point(point.name)
```

### `spec_context` Fixture (session-scoped)

For spec-driven limit derivation:

```python
def test_voltage(spec_context, dmm):
    # Get limit from product spec with guardband
    limit = spec_context.get_limit("output_voltage", temperature=25)
    value = dmm.measure_dc_voltage()
    # Framework checks against derived limit
```

Auto-discovers from `products/` directory or use `--spec` option.

### `run_context` Fixture (session-scoped)

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

### `logger` Fixture (session-scoped, autouse)

The underlying logger that captures all measurements. Automatically active for all tests. You rarely need to access it directly -- use `run_context` for custom metadata or `@litmus_test` for measurement capture.

### `mock_instruments` Fixture (session-scoped)

Returns `True` if `--mock-instruments` flag or `LITMUS_MOCK_INSTRUMENTS=1` environment variable is set. Rarely needed directly since auto-registered fixtures handle mock resolution automatically.

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
id: power_board_validation
name: "Power Board Validation"
test_phase: validation  # Required

steps:
  - name: power_on
    test: test_power.test_power_on
  - name: full_test
    sequence: detailed_power_tests  # Nested sequence inherits parent's phase
```

**Note:** When sequences call other sequences, the **root sequence** determines `test_phase` for the entire execution. Nested sequences' `test_phase` fields are ignored to ensure consistent phase across the call stack.

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
  --station=bench_1 \            # Station ID (default: station)
  --operator="Jane Doe" \        # Operator name
  --results-dir=./results \      # Results directory (default: results)
  --mock-instruments \           # Use mock instruments instead of real hardware
  --spec=products/x.yaml \  # Path to product spec YAML
  --guardband=10 \               # Default guardband percentage (default: 0)
  --station-config=stations/bench_1.yaml \  # Station config file
  --fixture-config=fixtures/x.yaml \        # Fixture config file
  --test-phase=validation \      # Test phase (default: auto-detect from git)
  -v
```

| Option | Default | Description |
|--------|---------|-------------|
| `--dut-serial` | `DUT001` | DUT serial number |
| `--station` | `station` | Station ID |
| `--operator` | `None` | Operator name |
| `--results-dir` | `results` | Directory for Parquet results |
| `--mock-instruments` | `False` | Use mock instruments instead of real hardware |
| `--spec` | `None` | Path to product spec YAML file |
| `--guardband` | `0` | Default guardband percentage |
| `--station-config` | `None` | Path to station configuration YAML |
| `--fixture-config` | `None` | Path to fixture configuration YAML |
| `--test-phase` | auto | Test phase (development, validation, characterization, production) |

## Markers

The plugin registers five Litmus markers. ``--strict-markers`` is on by
default (set in ``pyproject.toml``), so typos fail collection.

### `@pytest.mark.litmus_vectors(**kwargs)`

Parametrize vector inputs inline — an alternative to sidecar YAML. The
kwargs become parametrize values. Stacks with ``@pytest.mark.parametrize``.

### `@pytest.mark.litmus_limits(**kwargs)`

Inject limits by measurement name. Values merge with sidecar ``limits:``;
method-level markers override class-level markers by key.

### `@pytest.mark.litmus_spec(product)`

Override the session-wide spec for this test. Loads the named product via
``load_product`` and pushes a scoped ``SpecContext`` for the test body.

### `@pytest.mark.litmus_mocks(**kwargs)`

Patch instrument methods/attrs for the scope of the test. Kwargs are
``patch.object`` calls applied before the test body and unwound on
teardown.

### `@pytest.mark.litmus_independent`

Skip prereq-chain propagation for this test — failure does not mark
downstream tests as blocked.

### Retry and dependency skipping

For retries, use the ecosystem-standard
``@pytest.mark.flaky(reruns=N, reruns_delay=T)`` from
``pytest-rerunfailures`` (already a dependency). For explicit
dependency-based skipping, install ``pytest-dependency`` and use
``@pytest.mark.dependency(depends=["test_a"])``.

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
from litmus.execution import litmus_test

# No conftest boilerplate needed -- dmm is auto-registered from station config

@litmus_test
def test_input_voltage(context, dmm):
    """Verify input voltage."""
    return dmm.measure_voltage()

@litmus_test
def test_output_sweep(context, dmm):
    """Sweep load conditions."""
    # context.params["load_percent"] contains current load value
    return dmm.measure_voltage()
```

**Run:**
```bash
pytest tests/ --dut-serial=TEST001 -v
```

## Next Steps

- [Configuration Reference](configuration.md) — Detailed config options
- [Python Client](client.md) — Submit results from external tools
