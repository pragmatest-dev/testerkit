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

> **Concepts cheat-sheet.** Quick Start shows a complete Litmus project, which means it uses every concept the framework has — most for the first time. Each term in the rest of this page links forward to the tutorial step that introduces it properly:
>
> - **Part spec** — `parts/*.yaml`. Describes the device under test. → [Step 6](06-specifications.md), [concepts/parts](../concepts/configuration/parts.md)
> - **Station YAML** — `stations/*.yaml`. Declares the bench's instruments. → [Step 7](07-real-instruments.md), [concepts/stations](../concepts/configuration/stations.md)
> - **Sidecar YAML** — `tests/test_<module>.yaml`. Carries limits, sweeps, mocks for tests in that module. → [Step 5](05-configuration.md)
> - **`verify` / `measure` / `context` fixtures** — three of the 20 fixtures Litmus contributes. → [Step 3](03-fixtures.md), [reference/litmus-fixtures](../reference/pytest/fixtures.md)
> - **`@pytest.mark.litmus_limits`** — one of the seven Litmus markers; pins a limit at the top of a test. → [Step 4](04-limits.md), [reference/litmus-markers](../reference/pytest/markers.md)
> - **`mock_config`** — Per-instrument return values for mock mode. → [Step 2](02-mock-instruments.md), [how-to/mock-mode](../how-to/configuration/mock-mode.md)
> - **Characteristics, bands, accuracy, `when:`** — Part-spec vocabulary. → [Step 6](06-specifications.md), [reference/catalog-schema](../reference/catalog/schema.md)
> - **Capability matching** — How Litmus pairs a part to a station. → [Step 8](08-capabilities.md), [concepts/capabilities](../concepts/configuration/capabilities.md)
> - **MCP** — Model Context Protocol; how AI agents drive Litmus. → [how-to/mcp-integration](../how-to/overview/mcp-integration.md)

## How to Install

```bash
pip install litmus-test
```

## Project Structure

Litmus projects follow a standard folder structure. The UI is driven by these folders.

```
my_project/
├── parts/                    # WHAT you're testing
│   └── my_part.yaml          # Part specification
├── stations/                    # WHERE you test
│   └── my_station.yaml          # Instruments + addresses
├── fixtures/                    # HOW pins connect to instruments
│   └── my_fixture.yaml          # Pin-to-channel mappings
├── instruments/                 # Custom instrument drivers
│   └── custom_dmm.yaml          # Driver definitions
├── tests/                       # Test code + sidecar config
│   ├── conftest.py              # Custom fixtures (optional — roles auto-register)
│   ├── test_my_part.py       # Test functions
│   └── test_my_part.yaml     # Sidecar (vectors, limits, mocks)
├── results/                     # Output (gitignored)
│   └── measurements/            # Parquet files
└── pyproject.toml
```

## Understanding the Starter Project

When you run `litmus init quick_start --starter`, it generates all of these files. Here's what each one does:

### Part Spec (`parts/example_part.yaml`)

```yaml
# parts/my_part.yaml
id: my_part
name: "5V to 3.3V Power Module"

characteristics:
  output_voltage:
    function: dc_voltage
    direction: output
    units: V
    bands:
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

For real hardware, just remove `mock: true`. Litmus uses [PyVISA](https://pyvisa.readthedocs.io/) directly:

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

Or use [PyMeasure](https://pymeasure.readthedocs.io/) for high-level drivers (100+ instruments):

```yaml
instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
```

### Test Code (`tests/test_example.py`)

Tests are **plain pytest** — no decorator, no base class. The Litmus plugin contributes [20 fixtures](../reference/pytest/fixtures.md) (the per-test `context` / `verify` / `measure`, plus `pins`, `instruments`, per-role auto-fixtures from the station YAML, etc.) and [seven markers](../reference/pytest/markers.md). For how Litmus tests use pytest's own collection / fixture / marker mechanisms see [pytest-native reference](../reference/overview/pytest-native.md).

```python
# tests/test_my_part.py
class TestMyPart:
    def test_output_voltage(self, context, psu, dmm, verify):
        """Verify output voltage is within spec.

        verify() resolves the limit from the part YAML,
        records a measurement, and raises on fail.
        """
        vin = context.get_param("vin", 5.0)

        psu.set_voltage(vin)
        psu.enable_output()

        verify("output_voltage", dmm.measure_dc_voltage())
```

For measurements that don't come from the part spec, use `measure(name, value, limit={"low": ..., "high": ..., "units": "V"})` with inline limits or a sidecar `test_<module>.yaml`.

### Sidecar (`tests/test_my_part.yaml`)

Sidecar YAML carries vectors, limits, and mocks alongside the test file. Same merge rules as stacked pytest decorators — file scope, class scope, per-test:

```yaml
# tests/test_my_part.yaml
limits:
  output_voltage:
    low: 3.234
    high: 3.366
    nominal: 3.3
    units: V
tests:
  TestMyPart:
    sweeps:
      - {vin: [5.0]}
    mocks:
      - {target: dmm.measure_dc_voltage, return_value: 3.31}
```

### Running Tests

```bash
# Mock-instrument run (default for development)
pytest tests/ --station=my_station --mock-instruments --uut-serial=TEST001 -v

# With real hardware
pytest tests/ --station=my_station --uut-serial=SN001 -v
```

> **On `--uut-serial` for early articles:** if your first UUT doesn't have
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
4. **CHECK** with `verify(name, value)` or `measure(name, value, ...)` — never `assert 3.0 <= v <= 3.6`

```python
def test_something(context, psu, dmm, verify):
    vin = context.get_param("vin", 5.0)     # GET from context
    psu.set_voltage(vin)                    # SET UP
    psu.enable_output()
    verify("output_voltage",            # MEASURE + CHECK + RECORD
               dmm.measure_dc_voltage())
```

**No hardcoded values in code.** Conditions come from `context` (populated by native `@pytest.mark.parametrize` or sidecar YAML). Limits come from the part spec, an inline `@pytest.mark.litmus_limits` decorator, or the sidecar's `limits:` field — never inline asserts.

For the full reference — markers, sidecar YAML, `context.changed()`, mocks, retries — see the [Writing Tests guide](../how-to/execution/writing-tests.md).

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

# Each run writes one parquet at <data_dir>/runs/{date}/*.parquet
table = pq.read_table("data/runs")              # recurses into date subdirs
df = table.to_pandas()
print(df[df["record_type"] == "measurement"])   # measurement rows only
```

## Key Folders

| Folder | Purpose | UI Page |
|--------|---------|---------|
| `parts/` | Part specs (what you're testing) | /parts |
| `stations/` | Station configs (instruments + addresses) | /stations |
| `fixtures/` | Pin-to-instrument mappings | /fixtures |
| `instruments/` | Custom instrument drivers | /instruments |
| `tests/` | Test code + sidecar config | - |
| `data/` | Parquet + event log output (gitignored) | /runs |

## Optional: Set Up AI Assistance

If you use an AI coding tool, Litmus can register its [MCP (Model Context Protocol)](../how-to/overview/mcp-integration.md) server and generate project instructions so your AI understands the framework:

```bash
litmus setup claude-code       # Claude Code
litmus setup claude-desktop    # Claude Desktop
litmus setup copilot           # GitHub Copilot (VS Code + CLI)
```

## Next: Connect Real Hardware

When you're ready to move from mocks to real instruments, see [Step 7: Real Instruments](07-real-instruments.md). It covers station configuration, `litmus discover`, driver wiring, and common troubleshooting.

[Step 1: Run Something →](01-first-test.md)

## Next Steps

- [Tutorial index](index.md) — full step-by-step path (recommended next)
- [Core Concepts](../concepts/) — Understand parts, stations, and capabilities
- [Writing Tests](../how-to/execution/writing-tests.md) — Patterns and best practices
- [Configuration Reference](../reference/configuration.md) — YAML schema details
