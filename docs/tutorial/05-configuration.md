# Step 5: Test Configuration

**Goal:** Configure limits, vectors, and mocks for your tests.

## Where Test Config Lives

Test configuration (vectors, limits, mocks) can come from several places,
resolved in priority order:

1. **Pytest markers** — `@pytest.mark.parametrize(...)`, `@pytest.mark.litmus_limits`
2. **Sidecar YAML** — a `test_<module>.yaml` next to the test file

Markers and sidecar entries merge by name+key — later wins on overlap, the
same rule pytest applies to stacked decorators.

## Sidecar YAML

A **sidecar** is a YAML file next to your test module (`test_foo.py` → `test_foo.yaml`) carrying vectors, limits, and mocks for that file's tests. See [reference/configuration](../reference/configuration.md) for the full schema.

```yaml
# tests/test_power.yaml
limits:
  output_voltage: {low: 3.135, high: 3.465, nominal: 3.3, units: "V"}
mocks:
  - {target: dmm.measure_dc_voltage, return_value: 3.31}
tests:
  test_output_voltage:
    sweeps:
      - {vin: [4.5, 5.0, 5.5], load_current: [0.1, 0.4, 0.8]}
```

The test is then:

```python
# tests/test_power.py
def test_output_voltage(context, psu, dmm, verify):
    psu.set_voltage(context.get_param("vin"))
    psu.enable_output()
    verify("output_voltage", dmm.measure_dc_voltage())
```

Run directly with pytest:

```bash
pytest tests/test_power.py::test_output_voltage -v --uut-serial=TEST001
```

## Inline Markers

For inline tweaks, markers work directly on the test function:

```python
import pytest


@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
@pytest.mark.litmus_limits(output_voltage={"low": 3.135, "high": 3.465, "units": "V"})
def test_output_voltage(vin, context, psu, dmm, logger):
    psu.set_voltage(vin)
    psu.enable_output()
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

The [`@pytest.mark.litmus_sweeps(...)`](../reference/pytest/markers.md#litmus_sweeps) form is also available for inline use
of the runner-neutral vector vocabulary:

```python
@pytest.mark.litmus_sweeps([{"vin": [4.5, 5.0, 5.5], "load": [0.1, 0.4, 0.8]}])
def test_sweep(vin, load, psu, dmm, logger):
    ...
```

## Vector Expansion

Vectors define test conditions. They work identically inline and in sidecar.

```yaml
sweeps:
  - {input_voltage: [4.5, 5.0, 5.5]}
  - {load_percent: [0, 50, 100]}
```

Each top-level dict in the list is one independent loop; multi-key dicts
inside one entry zip together; stacked entries cross-product (top entry =
outermost / slowest loop). For zipped variables, put both keys in one
entry:

```yaml
sweeps:
  - {input_voltage: [4.5, 5.0, 5.5], load_percent: [0, 50, 100]}
```

```python
def test_voltage_sweep(context, dmm, logger):
    vin = context.get_param("input_voltage")
    load = context.get_param("load_percent")
    logger.measure("output_voltage", dmm.measure_voltage())
```

## Accessing Vector Parameters via Context

```python
def test_sweep(context, psu, dmm, logger):
    # Get required parameter (raises if missing)
    vin = context.get_param("input_voltage")

    # Get optional parameter with default
    load = context.get_param("load_percent", 0)

    # Get all parameters
    print(context.params)  # {"input_voltage": 5.0, "load_percent": 50}

    psu.set_voltage(vin)
    logger.measure("output_voltage", dmm.measure_voltage())
```

The context provides:
- `context.get_param("key")` - Required parameter (raises if missing)
- `context.get_param("key", default)` - Optional parameter with default
- `context.params` - All parameters as a dict

## Range Expanders

Any vector argvalues position accepts a range-expander dict that fans out
to a flat list at YAML load:

```yaml
sweeps:
  - {voltage: {linspace: [3.0, 5.0, 5]}}      # 5 evenly-spaced points
  - {frequency: {logspace: [1, 6, 6]}}        # 6 points 10^1 to 10^6
  - {soak_count: {repeat: [5.0, 100]}}        # 100 copies of 5.0
  - {pin: {range: [1, 17]}}                   # 1..16
```

Available expanders: `linspace`, `arange`, `logspace`, `geomspace`,
`repeat`, `range`. Same shape works in any list position across all Litmus
YAML (sidecars, profiles, stations, parts).

## Part with Change Detection

Put slow-changing parameters first. Use `context.changed(key)` — returns True iff this iteration's value differs from the previous iteration's — to detect outer loop changes:

```yaml
sweeps:
  - {temperature: [25, 85]}      # Outer (changes slowly)
  - {load: [0.1, 0.5]}           # Inner (changes fast)
```

```python
def test_temp_sweep(context, chamber, dmm, logger):
    if context.changed("temperature"):
        # Only reconfigure when temperature changes
        chamber.set_temp(context.get_param("temperature"))
        time.sleep(60)  # Wait for stabilization

    logger.measure("output_voltage", dmm.measure_voltage())
```

## Retries

For flaky tests, use the pytest ecosystem (the [`@pytest.mark.flaky`](https://github.com/pytest-dev/pytest-rerunfailures) marker is provided by `pytest-rerunfailures`):

```python
import pytest


@pytest.mark.flaky(reruns=3, reruns_delay=0.5)
def test_flaky(dmm, logger):
    logger.measure("voltage", dmm.measure_voltage())
```

This uses `pytest-rerunfailures` (already a Litmus dependency).

## What You Learned

- Config lives in markers (inline) or sidecar YAML (declarative)
- Markers and sidecar entries merge by name+key — later wins on overlap
- Vector expansion: cross-product across keys, zip via comma-joined argnames
- Range expanders (`linspace`, `arange`, `logspace`, …) for compact sweeps
- Accessing vector parameters via `context.get_param()` and `context.params`
- Using `context.changed()` for outer-loop detection
- Retries via `@pytest.mark.flaky`

## Continue

Where do these limit values come from? Let's link them to part specifications.

← [Step 4: Add Limits](04-limits.md)  |  [Step 6: Part Specifications →](06-specifications.md)
