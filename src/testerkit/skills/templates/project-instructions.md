# TesterKit — Hardware Test Platform

TesterKit is a Python-native hardware test platform for the AI-assisted era. It provides the infrastructure layer for hardware testing — configuration management (parts, stations, fixtures, profiles), instrument discovery and access (via PyVISA/PyMeasure), structured test data storage (Parquet), and AI tool integration (MCP server). Tests are standard pytest functions; TesterKit adds the hardware-specific context, data pipeline, and operator UI. Data flows from YAML config → pytest execution → Parquet results → reports/analytics.

## Folder Convention

Entity-aligned folders contain YAML configuration files:
- `parts/` — UUT specifications, characteristics, and limits
- `stations/` — Bench configurations (instruments + roles); `stations/types/` for abstract station-type templates
- `fixtures/` — UUT-pin to instrument-channel routing
- `profiles/` — Named bundles of session-level overrides (limits, sweeps, fixture, station_type)
- `catalog/` — Instrument capability definitions

Code folders contain Python scripts:
- `tests/` — pytest test files (with optional `<test_file>.yaml` sidecars next to each test file)
- `drivers/` — Custom instrument drivers (if needed)

## Common Commands

```bash
pytest                            # Run tests
pytest --mock-instruments         # Run with mock instruments (precedence: CLI > TESTERKIT_MOCK_INSTRUMENTS env > testerkit.yaml mock_instruments: > false)
pytest --station=my_bench         # Run against specific station (id or YAML path)
pytest --test-profile=production  # Apply a named profile
pytest --test-phase=production    # Select profile by facet

testerkit init [name] [--tier bringup|bench|factory]  # Scaffold a new project (skip-if-exists)
testerkit new-test <name>             # Scaffold tests/test_<name>.py from your station
testerkit validate [paths] [--json]   # Validate YAML config files
testerkit serve                       # Operator UI (localhost:8000)
testerkit serve --reload              # Dev mode with auto-reload
testerkit runs [--json]               # List recent test runs
testerkit show <run_id> [-f html|pdf|json|csv]  # Show run details / generate report
testerkit metrics summary [--json]    # Yield / pareto / ppk / trend / retest / time-loss (see `testerkit metrics --help`)
testerkit discover [--json]           # Scan for instruments
```

## YAML Configuration

All configuration uses YAML files with Pydantic validation. Edit YAML directly or use the operator UI (`testerkit serve`).

- **Parts** define what you're testing: characteristics, limits, pin map
- **Stations** define your bench: which instruments, what roles they play; `station_type:` declares the abstract layout
- **Fixtures** map UUT pins to instrument channels; `station_types: [...]` declares which station layouts the fixture supports
- **Profiles** bundle session-level overrides — limits, sweeps, mocks, fixture, station_type — keyed by facet (e.g. `test_phase: production`)

## Writing Tests

Tests are plain pytest functions. **Start with zero config** — the plugin always
provides these verbs; no YAML, station, or part spec is required to begin. The verbs:

- `observe(name, value)` — record a reading (characterization / setup readouts). Never judges.
- `verify(name, value, limit=...)` — judge a measurement against a limit. **The limit is
  required** — pass it inline (below), or supply it from a `<test_file>.yaml` sidecar or a part
  spec. `verify` with no resolvable limit raises.
- `measure` / `stream` — record-only variants (bare value / streaming samples).

Simplest passing test — **no config at all**:

```python
def test_output_voltage(verify) -> None:
    """Judge a reading against an inline limit — no station or part spec needed."""
    verify("output_voltage", 3.3, limit={"low": 3.0, "high": 3.6, "unit": "V"})

def test_rail_readout(observe) -> None:
    observe("rail_voltage", 3.28)  # record-only; no limit needed
```

**Instruments are opt-in.** Fixtures like `psu`/`dmm` are **not** built in — they come from an
active **station**'s `instruments:` map, or from the mock-instrument `conftest.py` that
`testerkit init --tier bringup` scaffolds. `--mock-instruments` swaps mock drivers in for a station's
declared roles; it does **not** invent `psu`/`dmm`. With a station (or the bringup scaffold):

```python
def test_output_voltage(verify, psu, dmm) -> None:
    psu.set_voltage(3.3)
    psu.enable_output()
    verify("output_voltage", float(dmm.measure_dc_voltage()),
           limit={"low": 3.0, "high": 3.6, "unit": "V"})
```

**Grow as needed** — a station, a part spec, a sidecar, or a profile only when the
request calls for it. Sidecar `<test_file>.yaml` keys (all optional): `limits:`,
`sweeps:`, `mocks:` (a list).

## Agent Skills

TesterKit ships Agent Skills — your assistant loads them automatically based on what
you're asking for. Reach for:

- `testerkit-tests` — test / measure / log a value (the front door; simple → advanced)
- `testerkit-stations` — set up a bench / wire an instrument
- `testerkit-parts` — spec a DUT's characteristics and limits
- `testerkit-mocks` — run without hardware
- `testerkit-profiles` — different limits/behavior per phase (dev vs production)
- `testerkit-sites` — test multiple units in parallel
- `testerkit-capture` — capture a waveform or file during a test
- `testerkit-data` — read/query/export runs, steps, measurements, channels, files
- `testerkit-analysis` — yield / Ppk / Pareto / trend metrics
- `testerkit-debug` — figure out why a run failed
- `testerkit-interactive` — guided/conversational test-writing on-ramp
- `testerkit-datasheets` — import a datasheet PDF into a catalog entry or part spec

## AI Agent Integration

**Prefer CLI with `--json` for tool use** — all commands above accept `--json` for machine-readable output. This is more token-efficient and reliable than MCP for local operations.

**MCP tools** (for remote/discovery use cases):
- `testerkit_project` — CRUD on parts, stations, fixtures, instruments, profiles, catalog
- `testerkit_schema` — JSON Schema for a YAML type (call before generating any YAML)
- `testerkit_open` — URL to view/edit an entity in the browser
- `testerkit_discover` — Discover instruments on VISA / NI / Serial / LXI buses
- `testerkit_match` — Check whether a station can test a part
- `testerkit_run` — Execute tests and stream results
- `testerkit_runs` / `testerkit_steps` / `testerkit_metrics` — Runs and steps tables; yield / pareto / ppk / trend / retest / time-loss analytics
- `testerkit_events` / `testerkit_sessions` / `testerkit_channels` / `testerkit_files` — Event log, sessions, channel data, and FileStore artifacts

**Reading test data** — ALWAYS go through a purpose-built surface: `testerkit runs`
/ `testerkit show <run_id> -f json` (CLI), the Query API (`RunsQuery` / `StepsQuery`
/ `MeasurementsQuery`), or the `testerkit_runs` / `testerkit_steps` / `testerkit_metrics`
MCP tools. **NEVER read `data/**/*.parquet` or `data/*/_index.*.duckdb`
directly** — that layout is internal and content-addressed (the fingerprint in
an index filename is a schema hash that renames on any version bump), so a
hand-rolled parquet/DuckDB reader breaks silently on upgrade. The `testerkit-data`
skill covers every read surface.
