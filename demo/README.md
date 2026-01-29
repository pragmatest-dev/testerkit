# Litmus Demo

This demo showcases the Litmus test framework with a simulated power board test.

## Quick Start

```bash
cd demo
python run_demo.py
```

## What's Included

- **specs/power_board.yaml** - Product spec with pins, characteristics, and test requirements
- **specs/minimal.yaml** - Minimal example showing how little you need to get started
- **tests/config.yaml** - Test configuration (vectors, limits) separate from code
- **tests/test_power_board.py** - Test suite using `@litmus_test` decorator

## Product Specification (ATML-style)

The power board spec demonstrates the new pin-based format:

```yaml
# specs/power_board.yaml
product:
  id: power_board
  name: "Demo Power Board"

pins:                        # Physical DUT connections
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    type: power
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
    type: signal

characteristics:             # What to test
  output_voltage:
    direction: output        # DUT provides this
    domain: voltage
    pins: [VOUT]            # Which pin(s)
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
```

## Test Patterns Demonstrated

### 1. Simple Measurement with File-Based Config

Test code is clean - configuration lives in `config.yaml`:

```python
# tests/test_power_board.py
@litmus_test
def test_input_voltage(vector, input_dmm):
    return input_dmm.measure_dc_voltage()
```

```yaml
# tests/config.yaml
test_input_voltage:
  limits:
    test_input_voltage:
      low: 4.5
      high: 5.5
      units: V
```

### 2. Vector Expansion (Multiple Test Cases)

Define test vectors in config, loop happens automatically:

```yaml
# config.yaml
test_output_stability:
  vectors:
    - sample: 1
    - sample: 2
    - sample: 3
  limits:
    test_output_stability:
      low: 3.135
      high: 3.465
      units: V
```

```python
@litmus_test
def test_output_stability(vector, output_dmm):
    return output_dmm.measure_dc_voltage()  # Runs 3 times
```

### 3. Nested Loops with Change Detection

```yaml
# config.yaml
test_temp_load_matrix:
  vectors:
    expand: nested
    loops:
      - name: temperature
        values: [25, 85]        # Outer loop
      - name: load
        values: [0, 50, 100]    # Inner loop
```

```python
@litmus_test
def test_temp_load_matrix(vector, output_dmm):
    if vector.changed("temperature"):
        set_chamber_temp(vector["temperature"])  # Only when temp changes
    return output_dmm.measure_dc_voltage()
```

### 4. Multiple Measurements (Dict Return)

```python
@litmus_test
def test_power(vector, input_dmm, output_dmm):
    return {
        "input_voltage": input_dmm.measure_dc_voltage(),
        "output_voltage": output_dmm.measure_dc_voltage(),
    }
```

### 5. Streaming Measurements (Yield)

```python
@litmus_test
def test_burn_in(vector, dmm):
    for minute in range(60):
        yield {"voltage": dmm.measure_dc_voltage()}
        time.sleep(60)
```

## Running Tests Manually

```bash
# From the demo directory
pytest tests/ --dut-serial=DPB001-0001 -v

# With custom options
pytest tests/ \
  --dut-serial=MY-SERIAL \
  --station=my_station \
  --operator="Test Engineer" \
  --results-dir=./my_results \
  -v
```

## Config File Format

The `config.yaml` file maps test function names to their configuration:

```yaml
test_function_name:
  vectors:                    # Optional: parameter combinations
    expand: product           # product, zip, range, or nested
    param1: [1, 2, 3]
    param2: [a, b]

  limits:                     # Optional: pass/fail limits
    measurement_name:
      low: 3.0
      high: 3.6
      nominal: 3.3
      units: V
      spec_ref: SPEC-001

  retry:                      # Optional: retry on failure
    max_attempts: 3
    delay_seconds: 0.5
```

## Querying Results

```python
import pyarrow.parquet as pq

# Read all measurements
table = pq.read_table("results/measurements")
for i in range(table.num_rows):
    print(f"{table.column('measurement_name')[i]}: {table.column('value')[i]}")

# Read test run summary
table = pq.read_table("results/test_runs")
print(f"Result: {table.column('outcome')[0]}")

# Read test vectors (parameter combinations)
table = pq.read_table("results/vectors")
for i in range(table.num_rows):
    print(f"Vector {table.column('index')[i]}: {table.column('params')[i]}")
```

## Test Limits

| Measurement | Nominal | Low | High | Units |
|-------------|---------|-----|------|-------|
| Input Voltage | 5.0 | 4.5 | 5.5 | V |
| Input Current | 0.010 | 0.005 | 0.015 | A |
| Output Voltage | 3.3 | 3.135 | 3.465 | V |
| Efficiency | 85 | 75 | 100 | % |
