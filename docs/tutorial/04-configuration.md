# Step 4: YAML Configuration

**Goal:** Move limits and test parameters into YAML files.

## What You'll Build

A test suite where limits come from configuration, not code.

## Project Structure

```
my_project/
├── specs/
│   └── power_board.yaml    # Product specification
├── tests/
│   ├── config.yaml         # Test configuration
│   └── test_power.py       # Test code
└── pyproject.toml
```

## Step 1: Create a Product Spec

Define what you're testing:

```yaml
# specs/power_board.yaml
product:
  id: power_board
  name: "5V to 3.3V Converter"

pins:
  VOUT:
    name: "J1.3"
    type: signal

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    units: V
    pins: [VOUT]
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
```

This spec says:
- The product has an output pin called VOUT
- VOUT should output 3.3V ± 5%

## Step 2: Create Test Configuration

Configure test limits:

```yaml
# tests/config.yaml
test_output_voltage:
  limits:
    output_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ tolerance_pct=5"
```

This configuration:
- Associates limits with the `test_output_voltage` function
- Specifies low/high limits
- References the spec for traceability

## Step 3: Write the Test

```python
# tests/test_power.py
from litmus.execution import litmus_test
from litmus.instruments import MockDMM

@litmus_test
def test_output_voltage(vector):
    """Verify output voltage is within spec."""
    with MockDMM(voltage=3.31) as dmm:
        return dmm.measure_voltage()
```

Notice:
- **No limits in the code!**
- The `@litmus_test` decorator loads config.yaml
- Return value is checked against configured limits

## Step 4: Run the Test

```bash
pytest tests/test_power.py -v --dut-serial=TEST001
```

The `--dut-serial` flag identifies the device under test.

## What's Happening

```
┌─────────────────────────────────────────────────────────────┐
│  1. pytest discovers test_output_voltage                    │
│                           │                                 │
│  2. @litmus_test loads config.yaml                         │
│                           │                                 │
│  3. Test function runs, returns Decimal("3.31")            │
│                           │                                 │
│  4. Return value → Measurement with configured limits       │
│                           │                                 │
│  5. check_limit() → Outcome.PASS                           │
│                           │                                 │
│  6. Results saved to Parquet                               │
└─────────────────────────────────────────────────────────────┘
```

## Vector Expansion

Config can also define test vectors (parameters):

```yaml
# tests/config.yaml
test_voltage_sweep:
  vectors:
    expand: product
    input_voltage: [4.5, 5.0, 5.5]
    load_percent: [0, 50, 100]
  limits:
    test_voltage_sweep:
      low: 3.135
      high: 3.465
      units: V
```

This runs the test 9 times (3 voltages × 3 loads):

```python
@litmus_test
def test_voltage_sweep(vector):
    """Run at multiple input voltages and loads."""
    input_v = vector["input_voltage"]
    load = vector["load_percent"]

    # Configure your test based on vector
    print(f"Testing at {input_v}V, {load}% load")

    with MockDMM(voltage=3.31) as dmm:
        return dmm.measure_voltage()
```

## Expansion Modes

| Mode | Description |
|------|-------------|
| `product` | Cartesian product (all combinations) |
| `zip` | Parallel iteration |
| `range` | Numeric range |
| `nested` | Nested loops with change detection |

### Example: zip

```yaml
vectors:
  expand: zip
  voltage: [3.3, 5.0, 12.0]
  current: [0.1, 0.5, 1.0]
# Creates 3 vectors: (3.3, 0.1), (5.0, 0.5), (12.0, 1.0)
```

### Example: range

```yaml
vectors:
  expand: range
  voltage:
    start: 3.0
    stop: 5.0
    step: 0.5
# Creates: 3.0, 3.5, 4.0, 4.5, 5.0
```

## Retry Configuration

Add retry behavior for flaky tests:

```yaml
test_output_voltage:
  limits:
    output_voltage:
      low: 3.135
      high: 3.465
  retry:
    max_attempts: 3
    delay_seconds: 0.5
```

If the test fails, it retries up to 3 times with 0.5s delay.

## Linking Specs to Limits

Instead of duplicating values, reference the spec:

```yaml
# tests/config.yaml
test_output_voltage:
  limits:
    output_voltage:
      ref: specs.power_board.characteristics.output_voltage
      guardband_pct: 10  # Tighten by 10% for manufacturing
```

The guardband tightens limits for manufacturing margin:
- Spec: 3.3V ± 5% = 3.135V to 3.465V
- With 10% guardband: 3.152V to 3.449V

## Benefits of YAML Configuration

1. **Separation of concerns** — Engineers change limits, not code
2. **Traceability** — Link limits back to specs
3. **Version control** — Track limit changes over time
4. **Non-developer access** — Technicians can adjust parameters
5. **Environment-specific** — Different limits for debug vs production

## What You Learned

- How to define product specs in YAML
- How to configure test limits externally
- Vector expansion for parametrized tests
- Retry configuration
- Linking limits to specifications

## Next Step

Time to connect to real instruments.

[Step 5: Real Instruments →](05-real-instruments.md)
