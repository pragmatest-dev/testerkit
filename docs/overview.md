# Litmus — Hardware Test Platform for the AI-Assisted Era

## Problems We Solve

| # | Problem | Who feels it |
|---|---------|-------------|
| 1 | **Starting a new test project takes weeks.** Every project rebuilds the same infrastructure: instrument drivers, config files, data storage, reporting. | Test engineers, managers |
| 2 | **"Can this bench test this board?" is a tribal knowledge question.** There's no way to check instrument coverage against product requirements without asking someone who's done it before. | Test engineers, NPI teams |
| 3 | **Changing a test limit means changing code.** Limits, sweep points, and instrument assignments are buried in Python or LabVIEW, so non-developers can't modify test behavior. | Test engineers, quality |
| 4 | **Test data is scattered and unlinked.** CSV files, SQL databases, Excel sheets — none connected to which instrument measured what, on which channel, with what calibration. | Quality, data analysts |
| 5 | **AI can't help because the tools are opaque.** Configuration lives in proprietary GUIs, instrument knowledge in people's heads, data in vendor-locked formats. | Everyone trying AI tools |
| 6 | **Vendor lock-in is expensive and sticky.** TestStand and LabVIEW work, but they're closed, costly, and impenetrable to modern dev workflows (git, CI, code review). | Management, developers |

## How Litmus Helps

| Problem | Benefit | Feature |
|---------|---------|---------|
| Starting takes weeks | **Go from datasheet to passing tests in a conversation.** AI reads your datasheet, builds the spec, discovers your instruments, generates test code, and runs it — with your approval at every step. | Datasheet-to-test workflow, `litmus init` scaffolding |
| Tribal knowledge matching | **Know instantly if your bench can test your board.** Automated matching compares product specs against instrument capabilities — range, accuracy, resolution — and tells you exactly what's missing. | Capability matching engine, 157-instrument catalog |
| Changing limits means code | **Test engineers edit YAML, not Python.** Limits, sweep vectors, instrument assignments, and station configs are all plain text files — validated on load, reviewable in PRs. | YAML config with Pydantic validation, VS Code schema support |
| Scattered unlinked data | **Every measurement traces back to its source.** DUT serial, instrument serial, calibration date, channel, operator, git commit — all in one row, queryable across runs. | Parquet storage, traceability model |
| AI can't help | **AI agents can read, write, and run every part of your test project.** MCP tools expose config, instruments, matching, and test execution. MCP tools expose config, instruments, matching, and test execution. Compatible with any MCP-capable tool. | MCP server, HTTP API |
| Vendor lock-in | **It's pytest, YAML, and Python.** No proprietary runtime, no license server, no binary config files. Everything is open, version-controlled, and built on libraries with massive ecosystems. | pytest integration, open-source stack |

## Design Principles

1. **Platform over framework** — Value is in infrastructure (config, instruments, data, AI tools), not test execution
2. **Integrate, don't reinvent** — Built on pytest, Pydantic, FastAPI, PyVISA, NiceGUI, PyArrow — libraries that LLMs already know
3. **Configuration as source of truth** — Non-developers modify test behavior without touching Python
4. **AI-ready, not AI-dependent** — Exposes MCP tools and HTTP APIs for external agents; the platform itself never calls an LLM
5. **Incremental adoption** — Start with the results API from existing tests, add config, add instruments, add AI tools

---

## Platform Features

### Capability matching

The matching engine answers three questions without powering anything on:

- **"Can this station test this product?"** — Compares product specs against instrument capabilities through range, accuracy, and resolution
- **"What instruments do I need?"** — Searches the catalog and recommends instruments that cover your requirements
- **"What's the gap?"** — Shows partial coverage with percentages, so you know exactly what to order

Matching is condition-aware: if your product needs 1% accuracy at 1 kHz but the spec only guarantees 5% above 100 kHz, Litmus catches that.

### Instrument catalog

157 instruments across 24 vendors (Keysight, Tektronix, Rigol, R&S, Siglent, Yokogawa, NI, and more), with structured capability data extracted from datasheets. Each entry describes what the instrument can do — functions, ranges, accuracy by operating condition, channels, and controls. Adding a new instrument is one command and a PDF.

### pytest integration

The `@litmus_test` decorator adds everything hardware tests need on top of standard pytest:

```python
@litmus_test
def test_output_voltage(context, psu, dmm):
    psu.set_voltage(context.get_param("vin"))
    return dmm.measure_dc_voltage()  # Just return the value
```

Tests return values — the framework handles logging, limit checking, and traceability. Return a single value, a dict of named measurements, or yield for streaming.

- **Sweep across conditions** — test at multiple temperatures, loads, and input voltages without code changes
- **Full traceability** — every measurement records what was measured, on what instrument, through which channel, from which DUT pin (see [What gets logged](#what-gets-logged) below)
- **Mock mode** — `pytest --mock-instruments` runs your full test suite without hardware
- **Retries** — configurable per-step, with delay and max attempts
- **Auto fixtures** — station instruments become pytest fixtures by role name (`dmm`, `psu`, `scope`)

### What gets logged

Every `context.measure()` call creates a record with:

| Category | Fields |
|----------|--------|
| **Measurement** | Name, value, units, limits (low/high/nominal), outcome (pass/fail/error), comparator |
| **Signal path** | DUT pin, fixture point, instrument name, instrument channel, VISA resource |
| **DUT** | Serial number, part number, revision, lot number |
| **Station** | Station ID, name, type, location |
| **Context** | Operator, test phase, sequence ID, git commit, vector parameters, attempt number, timestamp |
| **Config snapshots** | Station config, product spec, fixture config — frozen at run time for reconstruction |

All stored as Parquet with one row per measurement. Query across runs, instruments, and time periods. Reports export as HTML, PDF, JSON, or CSV.

### Operator UI

A browser application (`litmus serve`) for test operators and engineers:

- **Dashboard** — Active stations, recent runs, yield at a glance
- **Test launcher** — Select product, station, DUT serial; run with live progress
- **Analytics** — Yield summary, Pareto charts, CPK process capability, SPC trend monitoring
- **Fixture designer** — Visual drag-and-drop DUT pin-to-instrument wiring
- **Entity browsers** — Browse and edit products, stations, instruments, sequences, catalog entries

### Configuration that works with your tools

All configuration is stored as simple text files that work naturally with source control:

- **Test engineers** change limits, add sweep points, or swap instruments without writing code
- **Teams** review config changes in pull requests — no proprietary GUI diffs
- **VS Code** validates config files with auto-generated JSON Schemas (created by `litmus init`)

Every file is validated by a Pydantic model at load time — typos and schema violations are caught immediately, not at 2am during a production run.

### Adopt incrementally

You don't have to replace your test runner. Three paths to get data into Litmus:

**Keep your existing tests** — add a few lines to send measurements from LabVIEW, TestStand, or any language:

```python
from litmus.client import LitmusClient
client = LitmusClient()
run = client.start_run(dut_serial="ABC123", station_id="bench_1")
with run.step("measure_5v") as step:
    step.measure("rail_voltage", 5.02, units="V", low=4.75, high=5.25)
run.finish()
```

**Starting fresh?** Use `@litmus_test` with pytest for full integration from day one.

**Not Python?** HTTP API endpoints accept results from any language.

All three paths write to the same storage backend — unified analytics regardless of test source.

---

## AI Assistance

Litmus works without AI. With AI, it works faster.

### Datasheet to test project

The **datasheet-to-test** workflow is the fastest path from "I have a board" to "I have passing tests":

1. **Parse** — AI reads your datasheet, extracts pins, characteristics, and test conditions
2. **Spec** — Generates a product spec with typed electrical requirements (voltage ranges, accuracy at temperature, operating conditions)
3. **Station** — Discovers your bench instruments (pluggable discovery — VISA, LXI, and more), creates a station config, verifies capability coverage
4. **Tests** — Writes pytest code with measurement logging and limit checking
5. **Run** — Executes tests against real hardware or in mock mode for validation

Every step has an approval gate — the AI proposes, you decide. The typical path from datasheet to first passing test is one conversation.

### Datasheet to catalog entry

The **datasheet-to-catalog** skill reads instrument PDF datasheets and produces structured capability data:

1. **Split** PDF into sections by page range
2. **Extract** capabilities, channels, and specs from each section
3. **Write** structured data with signals, conditions, controls, and attributes
4. **Audit** against schema rules until clean

### MCP tools for AI agents

One setup command (`litmus setup claude-code`) gives AI assistants direct access to your test infrastructure:

| What the AI can do | How |
|---------------------|-----|
| Discover bench instruments | Pluggable discovery (VISA, LXI, and more) |
| Create station configs | Saves validated config files |
| Write product specs | Extracts from datasheets, validates against schema |
| Check bench compatibility | Runs capability matching |
| Run tests | Executes pytest, returns structured results |
| Open results in browser | Launches UI to specific entity |

Setup commands for Claude Code, Claude Desktop, Cursor, and Cline. Compatible with any MCP-capable tool. WSL-aware — auto-detects and configures Windows paths.

---

## Project Structure

Everything is organized by what it describes:

| Folder | What's in it | Who edits it |
|--------|-------------|--------------|
| `products/` | What you're testing — pins, specs, limits | Test engineers, AI |
| `stations/` | Your bench — instruments and addresses | Lab setup |
| `catalog/` | Instrument database — capabilities from datasheets | AI pipeline |
| `fixtures/` | Wiring — which DUT pin connects where | Fixture designer UI |
| `sequences/` | Test order — steps, vectors, retries | Test engineers |
| `instruments/` | Asset records — serial numbers, cal dates | Lab management |
| `tests/` | Test code — Python with `@litmus_test` | Developers, AI |

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  User Layer                                              │
│  pytest  ·  CLI  ·  Operator UI  ·  MCP Server           │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Platform Layer                                          │
│                                                          │
│  Config Loader          Capability Matching               │
│  Product Specs          Instrument Catalog                 │
│  Station Management     Fixture Designer                  │
│  Test Harness           Vector Expansion                  │
│  Measurement Logger     Instrument Discovery              │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Storage Layer                                           │
│  Parquet files · JSONL journals                           │
└──────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Models | Pydantic v2 |
| Config | YAML (ruamel.yaml) |
| Testing | pytest |
| Web UI | NiceGUI + Tailwind CSS |
| API | FastAPI |
| AI | MCP (fastmcp) |
| Instruments | PyVISA, vendor libs |
| Storage | Apache Parquet (PyArrow) |
| Reports | Jinja2 + WeasyPrint |
| Charts | ECharts (via NiceGUI) |
| CLI | Click |
| Packaging | uv |

## Current Status

- **157 catalog entries** across 24 instrument vendors
- **847 passing tests** with full CI coverage
- **Operator UI** with fixture designer, results browser, analytics
- **MCP integration** tested with Claude Code and Claude Desktop; setup commands for Cursor and Cline
- **VS Code support** — JSON Schema validation for YAML files via `litmus init`

## Getting Started

```bash
# Install from source (not yet on PyPI)
git clone https://github.com/pragmatest-dev/litmus.git && cd litmus && uv sync

litmus init my-project --discover # Scaffold project + auto-detect instruments
cd my-project
litmus serve                      # Start operator UI at localhost:8000
litmus setup claude-code          # Optional: configure AI assistant
```
