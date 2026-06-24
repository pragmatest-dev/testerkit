# Quick Start

Install Litmus, scaffold a starter project, and run a passing test. Requires Python 3.11+.

```bash
# 1. Install Litmus
pip install litmus-test

# 2. Create a starter project
litmus init quick_start --starter
cd quick_start

# 3. Run the test
pytest
```

The starter ships a single test (`test_output_voltage`). Run it and pytest reports `1 passed` — the measurement was taken against a mock instrument, checked against its limit, and recorded.

> **Explore without hardware.** [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/pragmatest-dev/litmus-starter) opens a browser sandbox to try Litmus with no install — mock-instrument tests, the operator UI, analytics, and AI integration. Real instrument control (PyVISA/serial to a bench) needs a local install, so the sandbox stops there.

> **Concepts cheat-sheet.** This quick start runs a complete Litmus project, so it touches nearly every Litmus concept at once — most for the first time. Each term below links forward to the tutorial step that introduces it properly:
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

## Project Structure

Litmus projects follow a standard folder structure. The UI is driven by these folders.

```
quick_start/
├── litmus.yaml                  # Project config (data_dir, default station, mock mode)
├── parts/                       # WHAT you're testing
│   └── example_part.yaml        # Part specification
├── stations/                    # WHERE you test
│   └── starter_station.yaml     # Instruments + addresses
├── fixtures/                    # HOW pins connect to instruments
│   └── example_fixture.yaml     # Pin-to-channel mappings
├── instruments/                 # Instrument asset records (identity, calibration)
│   ├── generic_psu_001.yaml
│   └── generic_dmm_001.yaml
├── tests/                       # Test code + sidecar config
│   ├── conftest.py              # Custom fixtures (optional — roles auto-register)
│   ├── test_example.py          # Test functions
│   └── test_example.yaml        # Sidecar (limits, sweeps, mocks)
├── data/                        # Output (gitignored)
└── pyproject.toml
```

## Understanding the Starter Project

When you run `litmus init quick_start --starter`, it generates all of these files. Here's what each one does:

### Part Spec (`parts/example_part.yaml`)

```yaml
# parts/example_part.yaml
id: "example_part"
name: "Example Part"
description: "Auto-generated example part specification"

pins:
  TP_VOUT: {name: "TP1", net: "VOUT_3V3", description: "Output voltage test point"}

characteristics:
  output_voltage:
    function: "dc_voltage"
    direction: "output"
    unit: "V"
    pin: "TP_VOUT"
    bands:
    - value: 3.3
      accuracy: {pct_reading: 2.0}
```

### Station Config (`stations/starter_station.yaml`)

```yaml
# stations/starter_station.yaml
id: "starter_station"
name: "Starter Station"
description: "Auto-generated starter station with mock instruments"

instruments:
  psu:
    type: "psu"
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true  # Start with mocks, switch to real hardware later
    mock_config: {set_voltage: null, enable_output: null, measure_voltage: 5.0, measure_current: 0.25}

  dmm:
    type: "dmm"
    resource: "TCPIP::192.168.1.101::INSTR"
    mock: true
    mock_config: {measure_dc_voltage: 3.3}
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

Tests are **plain pytest** — no decorator, no base class. Litmus's pytest integration contributes [20 fixtures](../reference/pytest/fixtures.md) (the per-test `context` / `verify` / `measure`, plus `pins`, `instruments`, per-role auto-fixtures from the station YAML, etc.) and [seven markers](../reference/pytest/markers.md). For how Litmus tests use pytest's own collection / fixture / marker mechanisms see [pytest-native reference](../reference/overview/pytest-native.md).

```python
# tests/test_example.py
def test_output_voltage(context, psu, dmm, verify) -> None:
    """Verify output voltage is within spec."""
    vin = context.get_param("vin", 5.0)
    psu.set_voltage(vin)
    psu.enable_output()
    # verify() resolves the limit from the sidecar / part YAML,
    # records the measurement, and raises on fail.
    verify("output_voltage", float(dmm.measure_dc_voltage()))
```

For measurements that don't come from the part spec, use `measure(name, value, limit={"low": ..., "high": ..., "unit": "V"})` with inline limits or a sidecar `test_<module>.yaml`.

### Sidecar (`tests/test_example.yaml`)

Sidecar YAML carries limits, sweeps, and mocks alongside the test file. A top-level key applies to every test in the module; per-test overrides go under `tests:`. The starter ships a per-test limit:

```yaml
# tests/test_example.yaml
tests:
  test_output_voltage:
    limits:
      output_voltage:
        low: 3.234
        high: 3.366
        unit: V
```

Sweeps and mocks live here too — e.g. a module-level `sweeps: [{vin: [5.0]}]` to parametrize, or `mocks: [{target: dmm.measure_dc_voltage, return_value: 3.31}]` (the starter instead sets mock returns in the station's `mock_config`).

### Running Tests

The starter's `pyproject.toml` bakes the station, mock mode, and a UUT serial into `addopts` (and `litmus.yaml` sets the same defaults), so the everyday command is just:

```bash
pytest
```

That expands to the explicit form below — useful when you want to override a default or run from outside the project:

```bash
# Mock-instrument run (the starter's default)
pytest tests/ --station=starter_station --mock-instruments --uut-serial=STARTER001 -v

# With real hardware (drop --mock-instruments; use a real serial)
pytest tests/ --station=starter_station --uut-serial=SN001 -v
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

Each run writes one parquet at `data/runs/{date}/*.parquet`. Measurements are
nested under the vector rows (`record_type = 'vector'`), so read them with a
DuckDB `UNNEST`:

```python
import duckdb

rows = duckdb.sql("""
    SELECT run_id, m.name, m.value, m.unit, m.outcome
    FROM read_parquet('data/runs/**/*.parquet', union_by_name=true),
         UNNEST(measurements) AS t(m)
    WHERE record_type = 'vector'
""").fetchall()
for row in rows:
    print(row)
```

For cross-run analytics (yield, Ppk, Pareto) use the higher-level
[`MeasurementsQuery`](../reference/data/query-api.md) API instead of reading
parquet directly.

## Key Folders

| Folder | Purpose | UI Page |
|--------|---------|---------|
| `parts/` | Part specs (what you're testing) | /parts |
| `stations/` | Station configs (instruments + addresses) | /stations |
| `fixtures/` | Pin-to-instrument mappings | /fixtures |
| `instruments/` | Instrument asset records (identity, calibration) | /instruments |
| `tests/` | Test code + sidecar config | - |
| `data/` | Parquet + event log output (gitignored) | /runs |

## Optional: Set Up AI Assistance

If you use an AI coding tool, Litmus can register its [MCP (Model Context Protocol)](../how-to/overview/mcp-integration.md) server and generate project instructions so your AI understands Litmus:

```bash
litmus setup claude-code       # Claude Code
litmus setup claude-desktop    # Claude Desktop
litmus setup copilot           # GitHub Copilot (VS Code + CLI)
```

## Next: Connect Real Hardware

When you're ready to move from mocks to real instruments, see [Step 7: Real Instruments](07-real-instruments.md). It covers station configuration, `litmus discover`, driver wiring, and common troubleshooting.

## Next Steps

You've seen the whole loop. From here:

- **Learn it from the ground up** → the [step-by-step tutorial](index.md) builds a project from nothing, one concept at a time. It's a separate path from this complete-project quick start, not a continuation of it.
- [Core Concepts](../concepts/) — Understand parts, stations, and capabilities
- [Writing Tests](../how-to/execution/writing-tests.md) — Patterns and best practices
- [Configuration Reference](../reference/configuration.md) — YAML schema details
