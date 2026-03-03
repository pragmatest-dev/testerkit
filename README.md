# Litmus

**Python hardware test platform for electronics production and validation.**

Litmus handles the parts of hardware testing that aren't your test: instrument management, result storage, limit checking, traceability, operator UI. You write pytest functions for your specific hardware. Everything else is config files and convention.

New products start fast. Results are consistent across product lines. Every measurement records which instrument took it.

```python
from litmus.execution import litmus_test

@litmus_test
def test_rail_3v3(context, psu, dmm):
    """Verify 3.3V output under load."""
    psu.set_voltage(context.get_in("vin"))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

```yaml
# config.yaml — limits and vectors easily modified without changing code
test_rail_3v3:
  vectors:
    expand: product
    vin: [3.3, 5.0, 12.0]
    load: [0.1, 0.5, 1.0]
  limits:
    test_rail_3v3:
      ref: products.power_board.rail_3v3
      guardband_pct: 10
```

Nine test vectors. Limits from your product spec with 10% guardband. Every measurement logged with instrument serial number, cal due date, and firmware version. If a unit fails in the field, you can trace back to the exact instrument and cal state that tested it.

## What you get

- **Instrument fixtures from station config** — Define roles once (`dmm`, `psu`, `eload`). They become pytest fixtures. No conftest.py boilerplate. Swap benches with `--station=bench_2`.
- **Develop without hardware** — `pytest --mock-instruments` returns configurable values per vector. Write and debug at your desk, plug in real instruments at the bench.
- **Limits from product specs** — Define specs once (nominal + tolerance), derive limits with optional guardbanding. Spec changes propagate everywhere.
- **Per-step instrument traceability** — Every result row records which instrument (serial, cal date, firmware) took that measurement. Not per-run — per-step.
- **Operator UI** — `litmus serve` gives operators a browser UI to pick sequences, enter serial numbers, and watch results. No CLI knowledge needed.
- **Capability matching** — Describe what signals your product needs. Litmus tells you which instruments in your catalog cover them.

## Quick start

```bash
pip install litmus
litmus init quick_start --starter && cd quick_start
pytest                          # runs with mock instruments out of the box
```

That's it. You have a working project with example tests, a station config, and mock instruments.

### What `--starter` generated

```yaml
# stations/station.yaml — instruments at this bench
station:
  id: demo_station
instruments:
  dmm:
    driver: drivers.Keithley2000
    resource: TCPIP::192.168.1.100::INSTR
  psu:
    driver: drivers.KeysightE36312A
    resource: TCPIP::192.168.1.101::INSTR
```

```python
# tests/test_power.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(psu, dmm):
    """Verify 5V rail under load."""
    psu.set_voltage(12.0)
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

`psu` and `dmm` come from your station config. No conftest.py needed.

### Next steps

Ready for real hardware? See [From Mocks to Hardware](docs/tutorial/from-mocks-to-hardware.md).

```bash
litmus discover                 # scan for real instruments
litmus station init             # assign roles interactively
litmus new-test output_voltage  # scaffold a new test
pytest --mock-instruments       # develop without hardware
pytest --station=my_bench       # run against real instruments
litmus runs                     # see results
```

### What results look like

Every measurement row in Parquet:

| Column | Example |
|---|---|
| `step_name` | `test_output_voltage` |
| `value` | `5.017` |
| `units` | `V` |
| `limit_low` / `limit_high` | `4.5` / `5.5` |
| `pass_fail` | `PASS` |
| `vin` | `12.0` |
| `instr_serial` | `["MY12345678"]` |
| `instr_cal_due` | `["2026-08-15"]` |
| `dut_serial` | `UNIT042` |

Open in pandas, DuckDB, or anything that reads Parquet.

## Project layout

```
products/*.yaml           → Product characteristics and tolerances
catalog/*.yaml            → Instrument capabilities and accuracy
stations/*.yaml           → Which instruments are at this bench
fixtures/*.yaml           → How DUT pins connect to instruments
sequences/*.yaml          → What to test and in what order
tests/*.py                → Test code
results/*.parquet         → Measurements with full traceability
```

Everything is files. That means it goes in git. You get diffs on limit changes, code review on test sequences, and a history of every config change.

## `@litmus_test` vs plain pytest

Plain `assert` for pass/fail checks:

```python
def test_power_on(psu):
    psu.enable_output()
    assert psu.get_status() == "ON"
```

`@litmus_test` when you need recorded measurements:

```python
@litmus_test
def test_rail_3v3(context, psu, dmm):
    psu.set_voltage(context.get_in("vin"))
    psu.enable_output()
    return dmm.measure_dc_voltage()
    # Return value → limit-checked → logged to Parquet with instrument identity
```

The decorator adds vector expansion, limit checking, measurement recording, retries, and mock value injection. Use it when you need any of that. Skip it when you don't.

## Capability matching

"We're bringing up a new board — do we have the instruments to test it?"

```python
litmus_match(requirements=[
    {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"},
    {"function": "dc_current", "direction": "output", "range_max": 3, "units": "A"},
])
# → Keysight 34461A covers dc_voltage input
# → Keysight E36312A covers dc_current output
```

Works from the CLI, MCP tools, or HTTP API.

## AI integration

Litmus exposes your test system as MCP tools. Optional, not a dependency.

```bash
litmus setup claude-code    # Add to Claude Code
litmus mcp serve            # Any MCP client
```

An agent can read a datasheet, extract specs, recommend instruments, generate configs, write tests, and run them — all through tool calls.

Convention-driven frameworks also produce better LLM output. When the pattern is always "return a measurement, limits come from config, instruments are fixtures," there's less room for the model to improvise poorly.

## Compared to alternatives

| | **TestStand** | **OpenHTF** | **In-house scripts** | **Litmus** |
|---|---|---|---|---|
| Language | LabVIEW/C# | Python | Varies | Python (pytest) |
| Config | Proprietary | Code | Scattered | Declarative files |
| License | $$$ | Free | — | Free |
| Instrument mgmt | Built-in | None | Manual | Config + catalog |
| Mock mode | Limited | Manual | Manual | `--mock-instruments` |
| Results | Proprietary | Protobuf | CSV/Excel | Parquet |
| AI tooling | No | No | No | MCP |
| Learning curve | Steep | Moderate | None (you wrote it) | pytest |

Closest to OpenHTF in spirit. pytest instead of a custom executor, config files instead of Python objects, Parquet instead of Protobuf.

## CLI

```bash
litmus init <name> [--starter]  # New project (--starter for full example)
litmus discover [--visa]        # Scan for instruments
litmus station init             # Interactive station setup
litmus new-test <name>          # Scaffold a test file
litmus serve [--reload]         # Operator UI
litmus runs / show <id>         # Results
litmus instrument list / show   # Instrument inventory
litmus mcp serve                # MCP server
litmus setup <tool>             # AI tool integration
```

## Docs

- [Architecture](./litmus-architecture.md) — How things connect
- [Roadmap](./docs/ROADMAP.md) — What's next
- [docs/](./docs/) — Guides and reference

## License

Apache 2.0