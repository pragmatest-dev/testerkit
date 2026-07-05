# Litmus — Hardware Test Platform

Litmus is a Python-native hardware test platform for the AI-assisted era. It provides the infrastructure layer for hardware testing — configuration management (parts, stations, fixtures, profiles), instrument discovery and access (via PyVISA/PyMeasure), structured test data storage (Parquet), and AI tool integration (MCP server). Tests are standard pytest functions; Litmus adds the hardware-specific context, data pipeline, and operator UI. Data flows from YAML config → pytest execution → Parquet results → reports/analytics.

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
pytest --mock-instruments         # Run with mock instruments (precedence: CLI > LITMUS_MOCK_INSTRUMENTS env > litmus.yaml mock_instruments: > false)
pytest --station=my_bench         # Run against specific station (id or YAML path)
pytest --test-profile=production  # Apply a named profile
pytest --test-phase=production    # Select profile by facet

litmus serve                      # Operator UI (localhost:8000)
litmus serve --reload             # Dev mode with auto-reload
litmus runs [--json]              # List recent test runs
litmus show <run_id>              # Show run details
litmus show <run_id> -f json      # JSON output (also: html, csv, pdf)
litmus discover [--json]          # Scan for instruments
litmus validate [paths] [--json]  # Validate YAML config files
litmus instrument list [--json]   # List configured instruments
litmus instrument show <id> [--json]  # Show instrument details + cal status
```

### Metrics (filters: `--since`, `--until`, `--part`, `--station`, `--phase`; all accept `--json`)

```bash
litmus metrics summary [--period day|week|month] [--json]
litmus metrics pareto [--top N] [--json]
litmus metrics ppk [--min-samples N] [--json]
litmus metrics trend [--period day|week|month] [--json]
litmus metrics retest [--period day|week|month] [--json]
litmus metrics time-loss [--period day|week|month] [--json]
```

## YAML Configuration

All configuration uses YAML files with Pydantic validation. Edit YAML directly or use the operator UI (`litmus serve`).

- **Parts** define what you're testing: characteristics, limits, pin map
- **Stations** define your bench: which instruments, what roles they play; `station_type:` declares the abstract layout
- **Fixtures** map UUT pins to instrument channels; `station_types: [...]` declares which station layouts the fixture supports
- **Profiles** bundle session-level overrides — limits, sweeps, mocks, fixture, station_type — keyed by facet (e.g. `test_phase: production`)

## Writing Tests

Tests are plain pytest functions. **Start with zero config** — the plugin always
provides these verbs; no YAML, station, or part spec is required to begin:

- `observe(name, value)` — record a reading (characterization / setup readouts). Never judges.
- `verify(name, value, limit=...)` — judge a measurement against a limit. **The limit is
  required** — pass it inline (below), or supply it from a `<test_file>.yaml` sidecar or a part
  spec (see the ladder). `verify` with no resolvable limit raises.
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
`litmus init --tier bringup` scaffolds. `--mock-instruments` swaps mock drivers in for a station's
declared roles; it does **not** invent `psu`/`dmm`. With a station (or the bringup scaffold):

```python
def test_output_voltage(verify, psu, dmm) -> None:
    psu.set_voltage(3.3)
    psu.enable_output()
    verify("output_voltage", float(dmm.measure_dc_voltage()),
           limit={"low": 3.0, "high": 3.6, "unit": "V"})
```

**Grow as needed** — adopt each rung only when you want it:

1. `observe(...)` — nothing.
2. `verify(..., limit={...})` — an inline limit.
3. `psu`/`dmm` + `--mock-instruments` — a station (or `litmus init --tier bringup`).
4. `verify("name", x)` with the limit from a spec — a part spec + `<test>.yaml` sidecar.
5. `--test-profile` / `--test-phase` — profiles.

Sidecar `<test_file>.yaml` keys (all optional): `limits:`, `sweeps:`, `mocks:` (a list). Run
`litmus refs show verify` for the exact schemas.

## AI Agent Integration

**Prefer CLI with `--json` for tool use** — all commands above accept `--json` for machine-readable output. This is more token-efficient and reliable than MCP for local operations.

**MCP tools** (for remote/discovery use cases):
- `litmus_project` — CRUD on parts, stations, fixtures, instruments, profiles, catalog
- `litmus_schema` — JSON Schema for a YAML type (call before generating any YAML)
- `litmus_open` — URL to view/edit an entity in the browser
- `litmus_discover` — Discover instruments on VISA / NI / Serial / LXI buses
- `litmus_match` — Check whether a station can test a part
- `litmus_run` — Execute tests and stream results
- `litmus_runs` / `litmus_steps` / `litmus_metrics` — Runs and steps tables; yield / pareto / ppk / trend / retest / time-loss analytics
- `litmus_events` / `litmus_sessions` / `litmus_channels` / `litmus_files` — Event log, sessions, channel data, and FileStore artifacts

**Test data** lives under `data/` (Parquet). Prefer the CLI / Query API
(`litmus runs`, `litmus show <run_id> -f json`) over raw parquet. For ad-hoc DuckDB:
```sql
SELECT * FROM 'data/runs/**/*.parquet' WHERE step_name = 'voltage_check'
```

## Reference Documentation

Read these on demand via the CLI — don't load them all upfront. `litmus refs list` shows available topics.

| Topic | Command |
|-------|---------|
| Project tiers (Tier 0 → 4 ladder, when to graduate) | `litmus refs show tiers` |
| `verify` signature, limit dict shape, sidecar `limits:` schema, outcomes | `litmus refs show verify` |
| `observe` / `stream` record-only verbs, ChannelStore / FileStore routing | `litmus refs show observe` |
| Per-test mock overrides (`litmus_mocks` marker + sidecar `mocks:`) | `litmus refs show mocks` |
| Profiles, facets, phase wiring | `litmus refs show profiles` |
