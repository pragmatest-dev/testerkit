# Step 5: Test Configuration

**Goal:** Configure limits, vectors, and mocks for your tests.

## Where Test Config Lives

Test configuration (vectors, limits, mocks) can come from two places, listed
lowest-priority first:

1. **Inline pytest markers** — `@pytest.mark.parametrize(...)`, `@pytest.mark.litmus_limits`
2. **Sidecar YAML** — a `test_<module>.yaml` next to the test file

When a marker and a sidecar entry set the same value, the **sidecar wins** —
they combine by test name and value name. (Profiles add a third layer that
overrides both; see [profiles](../how-to/execution/profiles.md).)

## Sidecar YAML

A **sidecar** is a YAML file next to your test module (`test_foo.py` → `test_foo.yaml`) carrying vectors, limits, and mocks for that file's tests. See [reference/configuration](../reference/configuration.md) for the full schema.

```yaml
# tests/test_power.yaml
limits:
  output_voltage: {low: 3.135, high: 3.465, nominal: 3.3, unit: "V"}
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
@pytest.mark.litmus_limits(output_voltage={"low": 3.135, "high": 3.465, "unit": "V"})
def test_output_voltage(vin, context, psu, dmm, measure):
    psu.set_voltage(vin)
    psu.enable_output()
    measure("output_voltage", dmm.measure_dc_voltage())
```

The [`@pytest.mark.litmus_sweeps(...)`](../reference/pytest/markers.md#litmus_sweeps) marker defines the same sweeps inline:

```python
@pytest.mark.litmus_sweeps([{"vin": [4.5, 5.0, 5.5], "load": [0.1, 0.4, 0.8]}])
def test_sweep(vin, load, psu, dmm, measure):
    ...
```

## Vector Expansion

Vectors define test conditions. They work identically inline and in sidecar.

```yaml
sweeps:
  - {input_voltage: [4.5, 5.0, 5.5]}
  - {load_percent: [0, 50, 100]}
```

Each entry in the list is its own loop. Stacked entries cross-product (the
top entry is the outermost, slowest loop). To sweep two values together
instead, put both in one entry — they zip:

```yaml
sweeps:
  - {input_voltage: [4.5, 5.0, 5.5], load_percent: [0, 50, 100]}
```

```python
def test_voltage_sweep(context, dmm, measure):
    vin = context.get_param("input_voltage")
    load = context.get_param("load_percent")
    measure("output_voltage", dmm.measure_voltage())
```

## Accessing Vector Parameters via Context

```python
def test_sweep(context, psu, dmm, measure):
    # Get a parameter (returns None if not set)
    vin = context.get_param("input_voltage")

    # Get a parameter with a fallback default
    load = context.get_param("load_percent", 0)

    # Get all parameters
    print(context.params)  # {"input_voltage": 5.0, "load_percent": 50}

    psu.set_voltage(vin)
    measure("output_voltage", dmm.measure_voltage())
```

The context provides:
- `context.get_param("key")` - The value, or `None` if it isn't set
- `context.get_param("key", default)` - The value, or `default` if it isn't set
- `context.params` - All parameters as a dict

## Range Expanders

Instead of listing every value, use a range-expander to generate the list:

```yaml
sweeps:
  - {voltage: {linspace: [3.0, 5.0, 5]}}      # 5 evenly-spaced points
  - {frequency: {logspace: [1, 6, 6]}}        # 6 points 10^1 to 10^6
  - {soak_count: {repeat: [5.0, 100]}}        # 100 copies of 5.0
  - {pin: {range: [1, 17]}}                   # 1..16
```

Available expanders: `linspace`, `arange`, `logspace`, `geomspace`,
`repeat`, `range`. They work anywhere a list of values is accepted — in
sidecars, profiles, stations, and parts.

## Part with Change Detection

Put slow-changing parameters first. Use `context.changed(key)` — True on the first iteration and whenever this iteration's value differs from the previous one — to skip work when an outer loop hasn't moved:

```yaml
sweeps:
  - {temperature: [25, 85]}      # Outer (changes slowly)
  - {load: [0.1, 0.5]}           # Inner (changes fast)
```

```python
def test_temp_sweep(context, chamber, dmm, measure):
    if context.changed("temperature"):
        # Only reconfigure when temperature changes
        chamber.set_temp(context.get_param("temperature"))
        time.sleep(60)  # Wait for stabilization

    measure("output_voltage", dmm.measure_voltage())
```

## Retries

A measurement can occasionally fail for a genuinely transient reason — a slow-settling rail, an intermittent comms link. The `litmus_retry` marker re-runs the test before recording a fail:

```python
import pytest


@pytest.mark.litmus_retry(max_retries=2, delay=0.5)
def test_voltage(dmm, measure):
    measure("voltage", dmm.measure_voltage())
```

`max_retries=2` allows up to two retries (three runs total); `delay` is the wait between them. In a sidecar, the same config goes under the test as a `retry:` block:

```yaml
tests:
  test_voltage:
    retry: {max_retries: 2, delay: 0.5}
```

Retries are for transient hardware conditions — not for masking a test that fails because something is genuinely wrong. (Under the hood, `litmus_retry` drives `pytest-rerunfailures`, a Litmus dependency.)

## What You Learned

- Config lives in inline markers or a sidecar YAML file
- When a marker and a sidecar entry set the same value, the sidecar wins
- Vector expansion: cross-product across keys, zip via comma-joined argnames
- Range expanders (`linspace`, `arange`, `logspace`, …) for compact sweeps
- Accessing vector parameters via `context.get_param()` and `context.params`
- Using `context.changed()` for outer-loop detection
- Retries via the `litmus_retry` marker

## Continue

Where do these limit values come from? Let's link them to part specifications.

← [Step 4: Add Limits](04-limits.md)  |  [Step 6: Part Specifications →](06-specifications.md)
