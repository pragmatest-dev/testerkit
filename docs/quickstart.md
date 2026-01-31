# Quick Start

Get up and running with Litmus in 5 minutes.

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/litmus.git
cd litmus

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e .
```

## Project Structure

Litmus projects follow a standard folder structure. The UI is driven by these folders.

```
my_project/
├── products/                    # WHAT you're testing
│   └── my_product/
│       └── spec.yaml            # Product specification
├── stations/                    # WHERE you test
│   └── my_station.yaml          # Instruments + addresses
├── fixtures/                    # HOW pins connect to instruments
│   └── my_fixture.yaml          # Pin-to-channel mappings
├── instruments/                 # Custom instrument drivers
│   └── custom_dmm.yaml          # Driver definitions
├── sequences/                   # Test execution order
│   └── full_validation.yaml     # Ordered test list
├── tests/                       # Test code
│   ├── conftest.py              # Fixture definitions
│   ├── config.yaml              # CONDITIONS + LIMITS
│   └── test_my_product.py       # Test functions
├── results/                     # Output (gitignored)
│   └── measurements/            # Parquet files
└── pyproject.toml
```

## Your First Test

### 1. Define the Product Spec

```yaml
# products/my_product/spec.yaml
product:
  id: my_product
  name: "5V to 3.3V Power Module"

characteristics:
  output_voltage:
    nominal: 3.3
    tolerance_pct: 5
    unit: V

test_conditions:
  default_vin: 5.0
  default_vout: 3.3
```

### 2. Configure the Station

```yaml
# stations/my_station.yaml
station:
  id: my_station
  name: "My Test Bench"

instruments:
  psu:
    type: psu
    resource: "TCPIP::192.168.1.101::INSTR"
    simulate: true
    sim_config:
      voltage: 5.0
      current: 0.1

  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.102::INSTR"
    simulate: true
    sim_config:
      voltage: 3.31  # Simulated output measurement
```

### 3. Create conftest.py

```python
# tests/conftest.py
import pytest

@pytest.fixture(scope="session")
def psu(instruments):
    """Power supply from station config."""
    return instruments.get("psu")

@pytest.fixture(scope="session")
def dmm(instruments):
    """DMM from station config."""
    return instruments.get("dmm")
```

### 4. Configure Test Conditions and Limits

**Both conditions (vectors) AND limits go in config.yaml:**

```yaml
# tests/config.yaml
test_output_voltage:
  vectors:
    - vin: 5.0  # Test condition from spec.test_conditions.default_vin
  limits:
    test_output_voltage:
      low: 3.135      # 3.3V - 5% (from spec)
      high: 3.465     # 3.3V + 5% (from spec)
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ tolerance_pct=5"
```

### 5. Write the Test

```python
# tests/test_my_product.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, psu, dmm):
    """Verify output voltage is within spec.

    The @litmus_test decorator:
    1. Loads vectors from config.yaml
    2. Loads limits from config.yaml
    3. Captures the return value as a measurement
    4. Checks against limits
    5. Records results to Parquet
    """
    # Get conditions from vector (not hardcoded!)
    vin = vector.get("vin", 5.0)

    # Set up stimulus
    psu.set_voltage(vin)
    psu.enable_output()

    # Measure and return - framework checks limits
    return dmm.measure_dc_voltage()
```

### 6. Run the Test

```bash
# With simulation (no hardware required)
pytest tests/ --station-config=stations/my_station.yaml --simulate --dut-serial=TEST001 -v

# With real hardware
pytest tests/ --station-config=stations/my_station.yaml --dut-serial=SN001 -v
```

## The Pattern

Every Litmus test follows this pattern:

1. **GET CONDITIONS** from vector (not hardcoded)
2. **SET UP** stimulus (PSU voltage, load current)
3. **MEASURE** the result
4. **RETURN** the value (framework checks limits from config.yaml)

```python
@litmus_test
def test_something(vector, psu, dmm):
    vin = vector.get("vin", 5.0)  # GET from vector
    psu.set_voltage(vin)          # SET UP
    psu.enable_output()
    return dmm.measure_dc_voltage()  # MEASURE and RETURN
```

**No hardcoded values in code.** Conditions come from vectors, limits from config.yaml.

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
| `sequences/` | Test execution order | /sequences |
| `tests/` | Test code + config.yaml | - |
| `results/` | Parquet output (gitignored) | /runs |

## Next Steps

- [Core Concepts](concepts.md) — Understand products, stations, and capabilities
- [Writing Tests](guides/writing-tests.md) — Patterns and best practices
- [Configuration Reference](reference/configuration.md) — YAML schema details
- [Tutorial](tutorial/index.md) — Step-by-step learning path
