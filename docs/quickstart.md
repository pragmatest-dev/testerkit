# Quick Start

Get up and running with Litmus in under a minute.

```bash
# 1. Install Litmus
pip install litmus-test

# 2. Create a starter project
litmus init quick_start --starter
cd quick_start

# 3. Run the tests
uv sync && pytest
```

That's it. You'll see tests pass with mock instruments, limits checked, and results recorded.

## How to Install

```bash
pip install litmus-test
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
├── tests/                       # Test code + sidecar config
│   ├── conftest.py              # Custom fixtures (optional — roles auto-register)
│   ├── test_my_product.py       # Test functions
│   └── test_my_product.yaml     # Sidecar (vectors, limits, mocks)
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

Tests are **plain pytest** — no decorator, no base class. The Litmus plugin contributes three fixtures (`context`, `spec`, `logger`) and a few markers:

```python
# tests/test_my_product.py
class TestMyProduct:
    def test_output_voltage(self, context, psu, dmm, spec):
        """Verify output voltage is within spec.

        spec.check() resolves the limit from the product YAML,
        records a measurement, and raises on fail.
        """
        vin = context.get_param("vin", 5.0)

        psu.set_voltage(vin)
        psu.enable_output()

        spec.check("output_voltage", dmm.measure_dc_voltage())
```

For measurements that don't come from the product spec, use `logger.measure(name, value, low=..., high=...)` with inline limits or a sidecar `test_<module>.yaml`.

### Sidecar (`tests/test_my_product.yaml`)

Sidecar YAML carries vectors, limits, and mocks alongside the test file. Same merge rules as stacked pytest decorators — file scope, class scope, per-test:

```yaml
# tests/test_my_product.yaml
limits:
  output_voltage:
    low: 3.234
    high: 3.366
    nominal: 3.3
    units: V
tests:
  TestMyProduct:
    sweeps:
      - {vin: [5.0]}
    mocks:
      - {target: dmm.measure_dc_voltage, return_value: 3.31}
```

### Running Tests

```bash
# Mock-instrument run (default for development)
pytest tests/ --station=my_station --mock-instruments --dut-serial=TEST001 -v

# With real hardware
pytest tests/ --station=my_station --dut-serial=SN001 -v
```

> **On `--dut-serial` for early articles:** if your first DUT doesn't have
> a real serial yet (engineering build, breadboard, dev unit), call it
> whatever you like — `bob`, `proto-1`, `dev`. The serial is just the
> identifier the run record will be filed under. Best practice once you
> have real units is to use the value that uniquely identifies what is
> being tested and measured (printed serial, scanned barcode, lot+sequence).

## The Pattern

Every Litmus test follows this pattern:

1. **GET CONDITIONS** from `context.get_param(...)` (not hardcoded)
2. **SET UP** stimulus (PSU voltage, load current)
3. **MEASURE** the result
4. **CHECK** with `spec.check(name, value)` or `logger.measure(name, value, ...)` — never `assert 3.0 <= v <= 3.6`

```python
def test_something(context, psu, dmm, spec):
    vin = context.get_param("vin", 5.0)     # GET from context
    psu.set_voltage(vin)                    # SET UP
    psu.enable_output()
    spec.check("output_voltage",            # MEASURE + CHECK + RECORD
               dmm.measure_dc_voltage())
```

**No hardcoded values in code.** Conditions come from `context` (populated by native `@pytest.mark.parametrize` or sidecar YAML). Limits come from the product spec, an inline `@pytest.mark.litmus_limits` decorator, or the sidecar's `limits:` field — never inline asserts.

For the full reference — markers, sidecar YAML, `context.changed()`, mocks, retries — see the [Writing Tests guide](guides/writing-tests.md).

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
| `tests/` | Test code + sidecar config | - |
| `results/` | Parquet output (gitignored) | /runs |

## Optional: Set Up AI Assistance

If you use an AI coding tool, Litmus can register its MCP server and generate project instructions so your AI understands the framework:

```bash
litmus setup claude-code       # Claude Code
litmus setup claude-desktop    # Claude Desktop
litmus setup copilot           # GitHub Copilot (VS Code + CLI)
```

## Next: Connect Real Hardware

When you're ready to move from mocks to real instruments, see [From Mocks to Hardware](tutorial/from-mocks-to-hardware.md). It covers discovering instruments, creating a real station config, and common troubleshooting.

## Next Steps

- [Core Concepts](concepts.md) — Understand products, stations, and capabilities
- [Writing Tests](guides/writing-tests.md) — Patterns and best practices
- [Configuration Reference](reference/configuration.md) — YAML schema details
- [Tutorial](tutorial/index.md) — Step-by-step learning path
