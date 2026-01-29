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

## Run the Demo

The fastest way to see Litmus in action:

```bash
cd demo
python run_demo.py
```

This runs a simulated power board test and displays the results.

## Your First Test

### 1. Create a Product Spec

Define what you're testing in `specs/my_product.yaml`:

```yaml
product:
  id: my_product
  name: "My Product"
  revision: "A"

characteristics:
  output_voltage:
    direction: output
    domain: voltage
    signal_types: [dc]
    units: V
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
```

### 2. Create a Station Config

Define your test station in `stations/my_station.yaml`:

```yaml
station:
  id: my_station
  name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    resource: "SIM::DMM"
    simulated: true
    sim_values:
      voltage: 3.31
```

### 3. Create a Test

Write your test in `tests/test_my_product.py`:

```python
import pytest
from litmus.execution import litmus_test
from litmus.instruments import DMM

@pytest.fixture
def dmm():
    with DMM("SIM::DMM", simulated=True, sim_values={"voltage": 3.31}) as d:
        yield d

@litmus_test
def test_output_voltage(vector, dmm):
    """Measure output voltage."""
    return dmm.measure_dc_voltage()
```

### 4. Configure Limits

Add test configuration in `tests/config.yaml`:

```yaml
test_output_voltage:
  limits:
    test_output_voltage:
      low: 3.135
      high: 3.465
      units: V
```

### 5. Run the Test

```bash
pytest tests/ --dut-serial=SN001 -v
```

Output:
```
tests/test_my_product.py::test_output_voltage PASSED
```

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

### Programmatic (Python)

```python
from litmus import LitmusClient

client = LitmusClient()
runs = client.list_runs()
print(runs[0])
```

### Raw Parquet

```python
import pyarrow.parquet as pq

# Read measurements
table = pq.read_table("results/measurements")
print(table.to_pandas())
```

## Next Steps

- [Core Concepts](concepts.md) — Understand products, stations, and capabilities
- [Configuration Reference](configuration.md) — YAML schema details
- [pytest Plugin Guide](pytest-plugin.md) — `@litmus_test`, vectors, retries
- [Python Client](client.md) — Submit results from external tools
- [API Reference](api.md) — MCP tools and HTTP endpoints
