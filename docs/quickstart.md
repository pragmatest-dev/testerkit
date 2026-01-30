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

## Simplest Test (With Logging)

Create a station config and write the test:

```yaml
# stations/my_station.yaml
station:
  id: my_station

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
```

```python
# tests/test_voltage.py
from litmus.execution.decorators import litmus_test

@litmus_test
def test_voltage(vector, instruments):
    """Measure voltage - result logged to Parquet."""
    dmm = instruments["dmm"]
    return dmm.measure_voltage()
```

Run with `--simulate` for simulated instruments:

```bash
pytest tests/ --station=my_station --simulate --dut-serial=TEST001 -v
```

Results appear in `results/` and via `litmus runs`.

## Without Logging (Plain pytest)

If you don't need Litmus logging, use mock instruments directly:

```python
# tests/test_simple.py
from litmus.instruments import MockDMM

def test_voltage():
    """Plain pytest - no logging to Litmus."""
    dmm = MockDMM(voltage=3.31)
    assert float(dmm.measure_voltage()) > 3.0
```

```bash
pytest tests/test_simple.py -v
```

This is just pytest with Litmus mock instruments — no fixtures, no logging.

## Adding Limits

For production testing, add limit checking:

```python
# tests/test_with_limits.py
from decimal import Decimal
from litmus.instruments import MockDMM
from litmus.data import Measurement, Outcome

def test_output_voltage():
    dmm = MockDMM(voltage=3.31)

    m = Measurement(
        name="output_voltage",
        value=dmm.measure_voltage(),
        units="V",
        low_limit=Decimal("3.135"),
        high_limit=Decimal("3.465"),
    )
    m.check_limit()

    assert m.outcome == Outcome.PASS
```

## Using YAML Config

For spec-driven testing with traceability:

### 1. Create a Product Spec

Define what you're testing in `specs/my_product.yaml`:

```yaml
product:
  id: my_product
  name: "My Product"

pins:
  VOUT:
    name: "J1.1"

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

### 2. Create a Station Config

Define your test station in `stations/my_station.yaml`:

```yaml
station:
  id: my_station
  name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    simulate: true
    sim_config:
      voltage: 3.31
```

### 3. Write the Test

```python
# tests/test_my_product.py
import pytest
from litmus.instruments import DMM

@pytest.fixture
def dmm():
    # Driver-level simulation (uses pyvisa-sim)
    with DMM("TCPIP::192.168.1.100::INSTR", simulate=True, sim_config={"voltage": 3.31}) as d:
        yield d

def test_output_voltage(dmm):
    """Measure output voltage."""
    voltage = dmm.measure_voltage()
    assert float(voltage) > 3.0
```

### 4. Add Test Config

Configure limits in `tests/config.yaml`:

```yaml
test_output_voltage:
  limits:
    output_voltage:
      low: 3.135
      high: 3.465
      units: V
```

### 5. Run the Test

```bash
pytest tests/ --dut-serial=SN001 -v
```

## Instrument Access Options

| Approach | Setup | When to Use |
|----------|-------|-------------|
| **`--simulate` flag** | None | Development, CI |
| **Station config** | `stations/*.yaml` | Real hardware |
| **Pin mapping** | `fixtures/*.yaml` | Production, complex routing |

### Station Config

Create `stations/my_station.yaml`:

```yaml
station:
  id: my_station
  name: "My Bench"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
```

Access instruments by name:

```python
@litmus_test
def test_voltage(vector, instruments):
    psu = instruments["psu"]
    dmm = instruments["dmm"]

    psu.set_voltage(5.0)
    psu.enable_output()
    return dmm.measure_voltage()
```

```bash
# Real hardware
pytest --station=my_station --dut-serial=SN001

# Simulated (same code, no hardware)
pytest --station=my_station --simulate --dut-serial=SN001
```

### Pin Mapping (Production)

For complex fixtures, create `fixtures/my_fixture.yaml`:

```yaml
fixture:
  id: my_fixture
  product_id: my_product

points:
  VIN:
    dut_pin: VIN
    instrument: psu
    instrument_channel: "1"
  VOUT:
    dut_pin: VOUT
    instrument: dmm
```

Access instruments by DUT pin name:

```python
@litmus_test
def test_output(vector, pins):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    return pins["VOUT"].measure_voltage()
```

```bash
pytest --station=my_station --fixture-config=fixtures/my_fixture.yaml --dut-serial=SN001
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
