# Vector Expansion

Vectors define the test conditions your tests run against. Litmus expands vectors from config.yaml and iterates over them, calling your test function for each combination.

## The Basics

A **Vector** is a dict of parameters for a single test iteration:

```python
@litmus_test
def test_output_voltage(vector, psu, dmm):
    vin = vector.get("vin", 5.0)      # Get parameter from vector
    load = vector.get("load", 0.1)    # With default fallback

    psu.set_voltage(vin)
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

Vectors are defined in `config.yaml`:

```yaml
test_output_voltage:
  vectors:
    - vin: 5.0
      load: 0.1
    - vin: 5.0
      load: 0.5
    - vin: 5.0
      load: 1.0
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
```

This runs the test 3 times—once for each vector.

## Expansion Modes

Instead of listing every combination, use expansion modes to generate vectors automatically.

### Mode 1: Explicit List (Default)

Just list your vectors:

```yaml
vectors:
  - vin: 5.0
    load: 0.1
  - vin: 5.0
    load: 0.5
  - vin: 12.0
    load: 1.0
```

### Mode 2: Product (Cartesian Product)

All combinations of parameters:

```yaml
vectors:
  expand: product
  vin: [4.5, 5.0, 5.5]
  load: [0.1, 0.5, 1.0]
```

Generates **9 vectors** (3 × 3):
```
{vin: 4.5, load: 0.1}, {vin: 4.5, load: 0.5}, {vin: 4.5, load: 1.0},
{vin: 5.0, load: 0.1}, {vin: 5.0, load: 0.5}, {vin: 5.0, load: 1.0},
{vin: 5.5, load: 0.1}, {vin: 5.5, load: 0.5}, {vin: 5.5, load: 1.0}
```

**Key insight:** First parameter is outermost loop (slowest changing), last is innermost (fastest changing).

### Mode 3: Zip (Parallel Iteration)

Pair parameters together (must have same length):

```yaml
vectors:
  expand: zip
  vin: [4.5, 5.0, 5.5]
  expected: [4.4, 4.9, 5.4]
```

Generates **3 vectors**:
```
{vin: 4.5, expected: 4.4},
{vin: 5.0, expected: 4.9},
{vin: 5.5, expected: 5.4}
```

### Mode 4: Range (Numeric Sweep)

Sweep a single parameter over a range:

```yaml
vectors:
  expand: range
  voltage:
    start: 0.0
    stop: 5.0
    step: 0.5
```

Generates **11 vectors**: 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0

Alternative with count instead of step:
```yaml
vectors:
  expand: range
  voltage:
    start: 0.0
    stop: 5.0
    count: 11  # Evenly spaced
```

### Mode 5: Nested (Fine-Grained Control)

For complex multi-level sweeps with explicit loop ordering:

```yaml
vectors:
  expand: nested
  loops:
    - name: temperature
      values: [-40, 25, 85]
    - name: voltage
      range:
        start: 3.0
        stop: 3.6
        step: 0.1
    - name: load
      values: [0.0, 0.5, 1.0]
```

Generates **63 vectors** (3 × 7 × 3):
- Temperature is outermost (changes slowest)
- Voltage is middle
- Load is innermost (changes fastest)

## Range String Syntax

Litmus supports a compact range syntax (SCPI-style, inclusive ranges):

| Syntax | Meaning | Example |
|--------|---------|---------|
| `"start:stop"` | Range with step=1 | `"1:4"` → [1, 2, 3, 4] |
| `"start:stop:step"` | Range with custom step | `"-40:85:25"` → [-40, -15, 10, 35, 60, 85] |
| `"a,b,c"` | Comma-separated values | `"3.3,5.0,12.0"` → [3.3, 5.0, 12.0] |
| `"a:b,c,d:e"` | Mixed ranges and values | `"0,0.5:2:0.5,5"` → [0, 0.5, 1.0, 1.5, 2.0, 5] |

Range strings work anywhere you'd use a list:

```yaml
# Product with range strings
vectors:
  expand: product
  voltage: "3.3:5.5:0.1"      # 23 values: 3.3, 3.4, ... 5.5
  temperature: "-40:85:25"    # 6 values: -40, -15, 10, 35, 60, 85

# Zip with range strings
vectors:
  expand: zip
  vin: "4.5:5.5:0.5"          # [4.5, 5.0, 5.5]
  expected: "4.4:5.4:0.5"     # [4.4, 4.9, 5.4]
```

**Note:** Ranges are **inclusive** of both start and stop (unlike Python's range). This matches hardware industry conventions (SCPI, Verilog, NI DAQmx).

## Change Detection with `vector.changed()`

When iterating through vectors, use `vector.changed()` to detect when outer-loop parameters change. This is useful for:

- Showing operator prompts only when temperature changes
- Re-initializing equipment on major parameter changes
- Minimizing expensive transitions

```python
@litmus_test
def test_with_temperature(vector, psu, dmm, chamber):
    temp = vector["temperature"]
    vin = vector["vin"]

    # Only change chamber when temperature changes
    if vector.changed("temperature"):
        chamber.set_temperature(temp)
        chamber.wait_for_stable()

    psu.set_voltage(vin)
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

With this config:
```yaml
vectors:
  expand: product
  temperature: [-40, 25, 85]  # Outer loop (slow)
  vin: [4.5, 5.0, 5.5]        # Inner loop (fast)
```

The chamber only changes 3 times (once per temperature), not 9 times.

### How `changed()` Works

- Returns `True` on the first vector (no previous to compare)
- Returns `True` if the value differs from the previous vector
- Returns `False` if the value is the same as the previous vector

## Zipped Variables in Nested Mode

For nested mode, you can zip multiple variables that should iterate together:

```yaml
vectors:
  expand: nested
  loops:
    - name: temperature
      values: [-40, 25, 85]
    - zip:  # These iterate together
        - name: vin
          values: [4.5, 5.0, 5.5]
        - name: vout_expected
          values: [3.2, 3.3, 3.4]
```

This generates 9 vectors where `vin` and `vout_expected` are always paired.

## Choosing the Right Mode

| Use Case | Mode | Why |
|----------|------|-----|
| Specific test points | Explicit list | Full control over each vector |
| All combinations of parameters | Product | Comprehensive coverage |
| Paired input/expected values | Zip | Keep related values together |
| Single parameter sweep | Range | Simple numeric sweeps |
| Multi-level sweeps with change detection | Nested | Fine-grained loop control |

## Performance Considerations

1. **Loop order matters in Product mode:** First parameter is outermost. Put expensive-to-change parameters (temperature, fixture setup) first.

2. **Use `vector.changed()` for expensive transitions:** Don't reconfigure equipment that didn't change.

3. **Nested mode gives explicit control:** When loop order matters for equipment transitions, use nested mode with explicit ordering.

4. **Range strings are efficient:** They're expanded at config load time, not during test execution.

## Complete Example

```yaml
# tests/config.yaml
test_load_regulation:
  vectors:
    expand: nested
    loops:
      - name: temperature
        values: [-40, 25, 85]
      - name: vin
        range:
          start: 4.5
          stop: 5.5
          step: 0.5
      - name: load_current
        values: "0.1:1.0:0.1"  # Range string: 0.1, 0.2, ... 1.0
  limits:
    test_load_regulation:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
```

```python
# tests/test_power.py
@litmus_test
def test_load_regulation(vector, psu, dmm, eload, chamber):
    temp = vector["temperature"]
    vin = vector["vin"]
    load = vector["load_current"]

    # Expensive: only when temperature changes
    if vector.changed("temperature"):
        chamber.set_temperature(temp)
        chamber.wait_for_stable(timeout=300)

    # Medium cost: only when vin changes
    if vector.changed("vin"):
        psu.set_voltage(vin)
        psu.enable_output()

    # Cheap: every iteration
    eload.set_current(load)
    eload.enable()

    return dmm.measure_dc_voltage()
```

This runs 90 tests (3 × 3 × 10) with minimal equipment transitions.
