# Quick Start

Get up and running with Litmus in under a minute.

```bash
# 1. Install Litmus (from source — not yet on PyPI)
git clone https://github.com/anthropics/litmus.git
cd litmus && uv sync

# 2. Create a starter project
litmus init quick_start --starter
cd quick_start

# 3. Run the tests
pytest
```

That's it. You'll see tests pass with mock instruments, limits checked, and results recorded.

## How to Install

> **Note:** Litmus is not yet published to PyPI. Install from source for now.

```bash
git clone https://github.com/anthropics/litmus.git
cd litmus && uv sync
```

## Project Structure

Litmus projects follow a standard folder structure. The UI is driven by these folders.

```
my_project/
├── products/                    # WHAT you're testing
│   └── my_product.yaml          # Product specification
├── stations/                    # WHERE you test
│   └── my_station.yaml          # Instruments + addresses
├── fixtures/                    # HOW pins connect to instruments
│   └── my_fixture.yaml          # Pin-to-channel mappings
├── instruments/                 # Custom instrument drivers
│   └── custom_dmm.yaml          # Driver definitions
├── sequences/                   # Test config + execution order
│   └── full_validation.yaml     # Steps with vectors, limits, mocks
├── tests/                       # Test code
│   ├── conftest.py              # Custom fixtures (optional — roles auto-register)
│   └── test_my_product.py       # Test functions
├── results/                     # Output (gitignored)
│   └── measurements/            # Parquet files
└── pyproject.toml
```

## Understanding the Starter Project

When you run `litmus init quick_start --starter`, it generates all of these files. Here's what each one does:

### Product Spec (`products/example_product.yaml`)

```yaml
# products/my_product.yaml
id: my_product
name: "5V to 3.3V Power Module"

characteristics:
  output_voltage:
    function: dc_voltage
    direction: output
    units: V
    specs:
      - when: {temperature: 25}
        value: 3.3
        accuracy: {pct_reading: 2.0}
```

### Station Config (`stations/starter_station.yaml`)

```yaml
# stations/my_station.yaml
id: my_station
name: "My Test Bench"

instruments:
  psu:
    type: psu
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true  # Start with mocks, switch to real hardware later
    mock_config:
      set_voltage: null      # No-op methods
      enable_output: null
      measure_voltage: 5.0   # Return values

  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.101::INSTR"
    mock: true
    mock_config:
      measure_dc_voltage: 3.31
```

For real hardware, just remove `mock: true`. Litmus uses PyVISA directly:

```yaml
# stations/bench_1.yaml - Real hardware
id: bench_1
name: "Test Bench 1"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    # No mock: true → uses PyVISA, fixture has .query()/.write()
```

Or use PyMeasure for high-level drivers (100+ instruments):

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
```

### Test Code (`tests/test_example.py`)

```python
# tests/test_my_product.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    """Verify output voltage is within spec.

    The @litmus_test decorator:
    1. Loads vectors and limits from the active sequence step
    2. Captures the return value as a measurement
    3. Checks against limits
    4. Records results to Parquet
    """
    # Get conditions from context (not hardcoded!)
    vin = context.get_in("vin", 5.0)

    # Set up stimulus
    psu.set_voltage(vin)
    psu.enable_output()

    # Measure and return - framework checks limits
    return dmm.measure_dc_voltage()
```

### Sequence (`sequences/example_sequence.yaml`)

Sequences are the **single source of truth** for test configuration. Each step carries its own vectors, limits, and mocks:

```yaml
# sequences/my_product_smoke.yaml
id: my_product_smoke
name: "My Product - Smoke Test"
product_family: my_product
test_phase: dev  # dev, validation, characterization, or production

steps:
  - id: output_voltage
    test: tests/test_my_product.py::test_output_voltage
    vectors:
      - vin: 5.0
    limits:
      output_voltage:
        low: 3.234
        high: 3.366
        nominal: 3.3
        units: V
    mocks:
      dmm.measure_dc_voltage: 3.31
```

### Running Tests

```bash
# With a sequence (production pattern)
pytest tests/ --sequence=my_product_smoke --station=my_station --mock-instruments --dut-serial=TEST001 -v

# Ad-hoc run without sequence (uses inline decorator defaults)
pytest tests/ --station-config=stations/my_station.yaml --mock-instruments --dut-serial=TEST001 -v

# With real hardware
pytest tests/ --sequence=my_product_smoke --station=my_station --dut-serial=SN001 -v
```

## The Pattern

Every Litmus test follows this pattern:

1. **GET CONDITIONS** from context (not hardcoded)
2. **SET UP** stimulus (PSU voltage, load current)
3. **MEASURE** the result
4. **RETURN** the value (framework checks limits from the active sequence step)

```python
@litmus_test
def test_something(context, psu, dmm):
    vin = context.get_in("vin", 5.0)  # GET from context
    psu.set_voltage(vin)              # SET UP
    psu.enable_output()
    return dmm.measure_dc_voltage()   # MEASURE and RETURN
```

**No hardcoded values in code.** Conditions come from context (populated by test vectors), limits from sequence steps.

## View Results

### CLI

```bash
litmus runs              # List recent runs
litmus show <run_id>     # Show run details
```

### Operator UI

```bash
litmus serve
# Open http://localhost:8000
```

### Programmatic

```python
import pyarrow.parquet as pq

table = pq.read_table("results/measurements")
print(table.to_pandas())
```

## Key Folders

| Folder | Purpose | UI Page |
|--------|---------|---------|
| `products/` | Product specs (what you're testing) | /products |
| `stations/` | Station configs (instruments + addresses) | /stations |
| `fixtures/` | Pin-to-instrument mappings | /fixtures |
| `instruments/` | Custom instrument drivers | /instruments |
| `sequences/` | Test config + execution order | /sequences |
| `tests/` | Test code | - |
| `results/` | Parquet output (gitignored) | /runs |

## Next: Connect Real Hardware

When you're ready to move from mocks to real instruments, see [From Mocks to Hardware](tutorial/from-mocks-to-hardware.md). It covers discovering instruments, creating a real station config, and common troubleshooting.

## Next Steps

- [Core Concepts](concepts.md) — Understand products, stations, and capabilities
- [Writing Tests](guides/writing-tests.md) — Patterns and best practices
- [Configuration Reference](reference/configuration.md) — YAML schema details
- [Tutorial](tutorial/index.md) — Step-by-step learning path
