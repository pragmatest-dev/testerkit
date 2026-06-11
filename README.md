# Litmus

[![PyPI](https://img.shields.io/pypi/v/litmus-test.svg)](https://pypi.org/project/litmus-test/)
[![Python](https://img.shields.io/pypi/pyversions/litmus-test.svg)](https://pypi.org/project/litmus-test/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)
[![CI](https://github.com/pragmatest-dev/litmus/actions/workflows/ci.yml/badge.svg)](https://github.com/pragmatest-dev/litmus/actions/workflows/ci.yml)

**Python hardware test platform for electronics validation and production.**

Litmus is a pytest plugin and command-line tool for hardware test engineers. You write tests as plain pytest functions; Litmus handles the parts that aren't your test — instrument setup, limit checking, results storage, operator UI. Tests run against mock instruments out of the box, so you can start without hardware and move to a real bench later.

## Get started in under a minute

```bash
pip install litmus-test            # or: uv add litmus-test
litmus init my_project --starter && cd my_project
pytest
```

Four tests pass against mock instruments. The [tutorial](./docs/tutorial/index.md) walks you from this starter project to a production-ready suite, one concept at a time.

## What `--starter` generated

```yaml
# stations/starter_station.yaml — mock instruments for getting started
id: starter_station
name: Starter Station
instruments:
  psu:
    type: psu
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true
    mock_config:
      set_voltage: null
      enable_output: null
      measure_voltage: 5.0
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.101::INSTR"
    mock: true
    mock_config:
      measure_dc_voltage: 3.3
```

```python
# tests/test_example.py
def test_output_voltage(context, psu, dmm, verify):
    """Verify output voltage is within spec."""
    vin = context.get_param("vin", 5.0)
    psu.set_voltage(vin)
    psu.enable_output()
    verify("output_voltage", float(dmm.measure_dc_voltage()))
```

`psu` and `dmm` come from your station config. `context` and `verify`
come from the Litmus pytest plugin. No conftest.py needed.

## Learning path

After the starter project runs, the recommended progression:

1. **[Tutorial](./docs/tutorial/index.md)** — Ten short chapters from a first test through live production monitoring. Read in order; each builds on the last.
2. **[Examples](./examples/README.md)** — Seven self-contained projects (`01-vanilla` → `07-profiles`) that isolate one concept each. Clone, run, modify.
3. **[Concepts](./docs/concepts/index.md)** — Reference for the vocabulary: station, fixture, part, sequence, capability, vector.

When you're ready to leave mocks behind, [From Mocks to Hardware](./docs/tutorial/from-mocks-to-hardware.md) covers the transition.

```bash
litmus discover                 # scan for real instruments
litmus station init             # assign roles interactively
litmus new-test output_voltage  # scaffold a new test
pytest --mock-instruments       # develop without hardware
pytest --station=my_bench       # run against real instruments
litmus runs                     # see results
```

## Design principles

1. **Built for hardware test, end to end** — Every measurement carries its limits, signal path, and the instrument that took it (with serial, cal date, firmware) — a field failure traces back to the exact bench state. Yield, Cpk, Pareto, retest, and time-loss analytics ship built in. Industry exporters (STDF, ATML, HDF5, TDMS, MDF4) bridge your reporting pipeline.
2. **Everything is a file you can version** — Limits, stations, parts, fixtures, sequences, results — all files. Edit them in your text editor, diff them in git, review changes like code. A project moves between machines as a folder.
3. **Open and extensible, no lock-in** — Pytest tests (plus its plugin ecosystem), PyVISA for any VISA-compatible instrument, YAML config, Parquet results that any data tool can read. All open source. If you change your mind about Litmus, your tests, configs, and results travel with you.
4. **AI-ready, never AI-dependent** — Built on technology AI assistants know deeply (pytest, YAML, Python, markdown). MCP tools expose every Litmus operation; JSON Schemas act as guardrails for any config the AI writes. The platform itself never calls out to an AI model.
5. **Starts simple, grows with you** — After install, `pytest` passes on any machine — no server, no account, no hardware needed to begin. Add what you need as you need it: measurement logging, station config, part specs, capability matching — in whatever order fits your project.

## Project layout

```
parts/*.yaml           → Part characteristics and tolerances
catalog/*.yaml            → Instrument capabilities and accuracy
stations/*.yaml           → Which instruments are at this bench
fixtures/*.yaml           → How DUT pins connect to instruments
sequences/*.yaml          → What to test and in what order
tests/*.py                → Test code
results/*.parquet         → Measurements with full traceability
```

Everything is files. That means it goes in git. You get diffs on limit changes, code review on test sequences, and a history of every config change.

## `verify()` vs plain `assert`

Plain `assert` for pass/fail checks:

```python
def test_power_on(psu):
    psu.enable_output()
    assert psu.get_status() == "ON"
```

`verify()` when you need recorded measurements:

```python
def test_rail_3v3(context, psu, dmm, verify):
    psu.set_voltage(context.get_param("vin"))
    psu.enable_output()
    verify("rail_3v3", float(dmm.measure_dc_voltage()))
    # → limit-checked against the YAML next to the test or your part spec
    # → logged to your results file with the instrument that took it
```

Sweeps, limits, mocks, and retries are all controlled from a YAML
file alongside the test — no extra wrapper code in your test function.

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

Run it from the command line, from an AI tool, or from a web request.

## AI integration

Connect Claude Code or any AI assistant to your test system. Optional, not required.

```bash
litmus setup claude-code    # Add to Claude Code
litmus mcp serve            # Any MCP-compatible AI tool
```

An AI assistant can read a datasheet, extract specs, recommend instruments, generate configs, and scaffold tests for you to review.

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

- [Quick start](./docs/tutorial/00-quickstart.md) — First project in 5 minutes
- [Architecture overview](./docs/concepts/architecture.md) — How things connect
- [docs/](./docs/) — Tutorial, how-to, reference, concepts

## License

Apache 2.0
