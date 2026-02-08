# Litmus - Hardware Test Framework

## Project Overview

Litmus is a Python-native hardware test **platform** for the AI-assisted era. It provides infrastructure for hardware testing—configuration management, instrument drivers, data storage, AI tool integration—regardless of which test runner you use.

**Primary path:** pytest-native framework for new projects
**Migration path:** OpenHTF adapter for existing test suites
**Catch-all:** Simple results API for any source (LabVIEW, TestStand, custom scripts)

## Core Philosophy

1. **Platform over framework** — Value is in infrastructure (config, instruments, data, tool exposure), not test execution
2. **Integrate, don't reinvent** — Use popular libraries (pytest, Pydantic, FastAPI, PyVISA) that LLMs know deeply
3. **Configuration as source of truth** — Non-developers can modify test behavior without touching code
4. **AI-ready, not AI-dependent** — Expose MCP tools and HTTP APIs for external agents; platform does NOT call LLMs
5. **Incremental adoption** — Start with results API, add config system, add instruments, add AI tools
6. **Run anywhere** — Local development, on-prem production, cloud-ready architecture

## Technology Stack

- **Language:** Python 3.11+
- **Test Execution:** pytest with custom plugin
- **Configuration:** YAML files with Pydantic validation
- **Web Framework:** FastAPI (API) + NiceGUI (UI)
- **Operator UI:** NiceGUI with Tailwind CSS
- **Instrument Communication:** PyVISA, pyserial
- **Data Storage:** Parquet files (local PoC), with plugin backends for PostgreSQL, InfluxDB, etc.
- **AI Integration:** MCP (Model Context Protocol) server + HTTP API

## Project Structure

```
litmus/
├── litmus/                    # Main package
│   ├── config/                # Configuration service
│   │   ├── models.py          # Pydantic models (Limit, Specification, StationConfig, FunctionCapability, etc.)
│   │   └── loader.py          # YAML loading and resolution
│   ├── catalog/               # Instrument capability catalog
│   │   ├── models.py          # InstrumentCatalogEntry
│   │   └── loader.py          # YAML catalog loading + catalog_ref resolution
│   ├── instruments/           # Instrument utilities (discovery, identity, mocks)
│   │   ├── base.py            # Base instrument class
│   │   ├── models.py          # InstrumentInfo, CalibrationInfo, InstrumentRecord
│   │   ├── discovery.py       # discover_visa(), get_info_visa(), register_protocol()
│   │   ├── loader.py          # Load instrument/station YAML files
│   │   ├── mocks.py           # Generic Mock factory for any driver class
│   │   └── visa.py            # VisaInstrument protocol base class
│   ├── execution/             # Test execution engine
│   │   ├── plugin.py          # pytest plugin
│   │   └── fixtures.py        # pytest fixtures
│   ├── data/                  # Data/logging service
│   │   ├── models.py          # Result models
│   │   └── backends/          # Storage backends
│   ├── mcp/                   # MCP server
│   │   └── server.py          # MCP tool definitions
│   ├── api/                   # HTTP API
│   │   ├── app.py             # FastAPI + NiceGUI app factory
│   │   └── models.py          # API request/response models
│   ├── ui/                    # Operator UI
│   │   ├── app.py             # NiceGUI pages (dashboard, launch, results)
│   │   └── static/            # Static assets (global.css)
│   └── cli.py                 # CLI entry point
│   └── skills/               # AI workflow prompts
├── products/                  # Product folders
│   └── {product_id}/
│       ├── manifest.yaml      # Workflow position
│       ├── datasheet.md       # Source document
│       └── spec.yaml          # Product specification
├── catalog/                   # Instrument capability catalog (YAML: vendor/model capabilities)
├── instruments/               # Instrument inventory (YAML: identity + calibration per asset)
├── stations/                  # Station assignments (YAML: role→instrument, resources)
├── drivers/                   # User's instrument driver classes (Python)
├── sequences/                 # Test sequences (YAML)
├── fixtures/                  # Test fixture definitions (YAML: DUT pin→instrument routing)
├── tests/                     # Test suites
│   ├── conftest.py            # pytest configuration
│   └── test_*.py              # Test files
├── results/                   # Parquet output (gitignored)
├── litmus.yaml                # Project-level configuration
├── pyproject.toml             # Python project configuration
└── litmus-architecture.md     # Architecture specification
```

## Key Models

- `Limit` - Test limit with units and spec reference
- `Specification` - Product specification that limits derive from
- `InstrumentInfo` - Instrument identity (manufacturer, model, serial, firmware)
- `CalibrationInfo` - Calibration tracking (due date, certificate, lab)
- `InstrumentRecord` - Complete instrument record (info + calibration + resource)
- `InstrumentConfig` / `InstrumentInstance` - Instrument configuration
- `StationType` / `StationInstance` - Station templates and instances
- `FixtureConfig` / `FixturePoint` - Test fixture definitions
- `PromptConfig` - Operator prompt configuration (YAML-driven)
- `Dialog` / `DialogManager` - Runtime operator dialogs (confirm, choice, input, image)
- `TestStepConfig` / `TestSequenceConfig` - Test configuration
- `FunctionCapability` - Instrument capability with measurement function, direction, and named signal parameters
- `MeasurementFunction` - Named signal functions (dc_voltage, ac_voltage, resistance, waveform, etc.)
- `SignalParameter` - Per-parameter range, accuracy, resolution specs
- `InstrumentCatalogEntry` - Vendor/model instrument catalog with capabilities, structured channel topology, and optional `base` for variant inheritance
- `PinRole` - Pin role enum (signal/ground/power/reference) on product Pin model
- `ChannelTopology` - Structured channel description (terminals, connector, ground topology)
- `TerminalRole` - Physical terminal types (hi/lo/sense_hi/sense_lo/guard/signal/trigger)
- `GroundTopology` - Channel ground mode (floating/shared/earth)

## Common Commands

```bash
# Development
uv sync                        # Install dependencies
pytest                         # Run tests
pytest --cov=litmus            # Run tests with coverage

# Linting and formatting
ruff check .                   # Lint code
ruff format .                  # Format code

# Operator UI
litmus serve                   # Start operator UI (default: http://localhost:8000)
litmus serve --port 8080       # Custom port
litmus serve --reload          # Auto-reload for development

# CLI tools
litmus runs                    # List recent test runs
litmus show <run_id>           # Show details for a test run

# Instrument discovery (setup time, slow)
litmus discover                # Scan all protocols (VISA, NI, serial)
litmus discover --visa         # VISA only
litmus discover --no-identify  # Skip *IDN? queries (faster)

# Station management
litmus station init            # Interactive: discover → assign roles → save
litmus station validate <id>   # Verify instruments match config
litmus station update <id>     # Re-discover and update instrument files

# Instrument management
litmus instrument list         # Show all instrument files
litmus instrument show <id>    # Show instrument details + calibration
litmus instrument cal <id>     # Update calibration info

# MCP server
litmus mcp serve               # Start MCP server
```

## Configuration Patterns

### Folder Convention
Entity-aligned folders contain YAML configuration files. Code folders contain Python scripts.
- **YAML config**: `catalog/`, `instruments/`, `stations/`, `products/`, `fixtures/`, `sequences/`
- **Python code**: `drivers/`, `tests/`

### Capability Model (ATML/IEEE 1641-inspired)

Capabilities use a **signal-parameter model** where `MeasurementFunction` is the primary discriminator:

```python
class FunctionCapability(BaseModel):
    function: MeasurementFunction    # dc_voltage, ac_voltage, resistance, waveform, etc.
    direction: Direction             # input (measure) or output (source)
    parameters: dict[str, SignalParameter]  # Named params with range/accuracy/resolution
    channels: list[str]             # Channel keys (e.g., ["1", "2", "3"])
    readback: bool = False          # True for built-in meters (PSU voltage readback)
```

**Matching algorithm** (3-tier):
1. Function match — same `MeasurementFunction`
2. Direction match — DUT OUTPUT ↔ instrument INPUT (direction flip)
3. Parameter range containment — instrument range must contain required value/range

### 3-Tier Instrument Configuration

Instruments are defined in three layers:
1. **Catalog files** (`catalog/*.yaml`) — Universal: what a MODEL can do (capabilities with ranges/accuracy)
2. **Instrument files** (`instruments/*.yaml`) — Per-asset: serial, calibration, `catalog_ref`
3. **Station files** (`stations/*.yaml`) — Project-local: role assignments, driver, resource addresses, `catalog_ref`

Driver lives on station/asset config, NOT catalog. Catalog is shareable across projects.

**Variant inheritance** (`base` field): Catalog entries can inherit from a base entry to avoid YAML duplication. Set `base: <base_id>` on the variant's `catalog_entry`. Merge is section-level: variant `capabilities:` or `channels:` fully replace base's; header fields (`manufacturer`, `instrument_class`) are inherited when absent. Supports chains (A→B→C, max depth 5) with cycle detection.

### Catalog Scope: All Testable Instruments

The catalog includes **all testable instruments with documented capabilities**, not just those with dedicated Python drivers:

**Included instruments:**
- Instruments with dedicated Python drivers (PyMeasure, InstrumentKit, vendor SDKs)
- Generic SCPI instruments (controlled via PyVISA raw commands)
- Vendor SDK instruments (NI-DAQmx, Keysight IO Libraries, etc.)

**Key principle:** Capabilities are the source of truth. The catalog `driver` field is **informational** (indicates available automation level), while the station `driver` field is **operational** (required for actual control).

**Catalog driver field semantics:**
- `"pymeasure.instruments.Foo"` — Dedicated driver available
- `null` or omitted — SCPI or SDK control (user provides wrapper)
- `"nidaqmx"` — Vendor SDK required

**Why SCPI instruments matter:**
- Thousands of SCPI-compliant instruments exist with documented capabilities
- SCPI is actually **easier for AI code generation** (standardized commands)
- Enables capability-based matching and purchase recommendations regardless of driver availability
- Users can mock SCPI instruments immediately, then swap to real hardware later

See `agent-os/specs/2026-02-06-scpi-catalog-architecture/spec.md` for complete architecture documentation.

### Test Configuration
Tests use YAML configs (`tests/test_*/config.yaml`) that define:
- Test sequences with steps
- Operator dialogs
- Limit references to specs
- Retry behavior

### Specs to Limits
Product specifications (`products/{id}/spec.yaml`) define nominal values and tolerances. Test limits are derived from specs with optional guardbanding:
```python
spec.to_limit(guardband_pct=10.0)
```

## Development Guidelines

- Use Pydantic models for all configuration and data structures
- Prefer YAML for human-editable configuration files
- Litmus does NOT provide instrument drivers — users bring their own (PyMeasure, PyVISA, vendor libs)
- User driver classes live in `drivers/` (Python), instrument assets in `instruments/` (YAML)
- All MCP tools should have equivalent HTTP API endpoints
- Results use a consistent schema across all storage backends
- Operator UI uses NiceGUI for reactive Python-native interfaces
- API routes use FastAPI for JSON endpoints (consumed by CLI, MCP, external systems)
- Style UI components with Tailwind CSS classes via `.classes()`

## Testing Approach

- Use pytest as the test runner
- Custom pytest plugin provides fixtures for instruments, config, and dialogs
- Test configuration is separate from test code
- Support for retry logic, skip conditions, and operator prompts

## Instrument Fixture Auto-Registration

The Litmus pytest plugin **automatically registers a session-scoped fixture for each instrument role** defined in the station config. This eliminates conftest boilerplate — users write tests that directly use role names as fixture parameters.

### How It Works

1. At `pytest_configure` time, the plugin calls `_find_station_file()` to locate the station YAML
2. Parses the `instruments:` section to get role names (e.g., `dmm`, `psu`, `eload`, `scope`)
3. For each role, creates a `@pytest.fixture(scope="session")` that delegates to `instruments[role]`
4. Registers them as a pytest plugin via `config.pluginmanager.register()`

Because plugin-registered fixtures have the lowest precedence, a user-defined fixture with the same name in `conftest.py` naturally takes precedence — standard pytest override behavior.

### Implementation Location

- `litmus/execution/plugin.py`: `_find_station_file()`, `pytest_configure()` (auto-registration), `InstrumentAccessor` class, `instrument` fixture
- Station config: `stations/{station_id}.yaml` with `instruments:` section mapping role → driver + resource

### User Experience

**Zero-boilerplate tests** — role names from station config are directly available as fixtures:

```python
# No conftest.py fixture definitions needed
def test_voltage(dmm, psu):
    psu.set_voltage(5.0)
    psu.enable_output()
    assert dmm.measure_dc_voltage() > 3.0
```

**Override pattern** — define a fixture with the same name in conftest to customize:

```python
# conftest.py — only needed for custom setup/teardown
@pytest.fixture(scope="session")
def psu(instruments):
    inst = instruments.get("psu")
    inst.set_voltage(5.0)  # project-specific default
    return inst
```

**InstrumentAccessor** — the `instrument` fixture provides programmatic access:

```python
def test_dynamic(instrument):
    dmm = instrument("dmm")           # Get by role
    roles = instrument.roles()         # List all roles
    dmms = instrument.by_type("drivers.Keithley2000")  # Group by driver
```

### conftest.py Guidelines

With auto-registration, conftest.py should only contain fixtures that add **semantic value** beyond simple role access:
- Pin-based fixtures (`output_dmm`, `input_psu`) that use the `pins` fixture for DUT traceability
- Custom instrument setup/teardown that differs from the default
- Project-specific test utilities

Do NOT add boilerplate like `def dmm(instruments): return instruments.get("dmm")` — the plugin handles this automatically.

### Per-Step Instrument Aliases

Sequence steps can define `aliases: {dmm: precision_dmm}` to remap fixture names to different station instruments per step. Without aliases, fixture names fall through to station role names (zero overhead). The `--sequence` pytest option passes sequence context to the plugin. See `docs/guides/writing-sequences.md`.

## Per-Step Instrument Identity in Parquet

Each Parquet row includes 13 `instr_*` columns (parallel arrays) identifying the instruments used by that test step. Only the instruments a test actually uses are included — auto-detected from fixture parameters.

### How It Works

1. `@litmus_test` in `litmus/execution/decorators.py` detects which instrument roles appear in kwargs (matched against `_INSTRUMENT_RECORDS`)
2. Calls `logger.set_step_instruments(roles)` to filter and cache the instrument arrays
3. The journal writer receives the per-step arrays for each subsequent measurement row
4. For the non-journal path, `step.instrument_arrays` carries the data through `TestStep`

### Column List

`instr_name`, `instr_id`, `instr_driver`, `instr_resource`, `instr_protocol`, `instr_manufacturer`, `instr_model`, `instr_serial`, `instr_firmware`, `instr_cal_due`, `instr_cal_last`, `instr_cal_certificate`, `instr_cal_lab`

### Key Files

- `litmus/execution/logger.py`: `build_instrument_arrays(roles=)`, `set_step_instruments()`
- `litmus/execution/decorators.py`: Per-step detection in `@litmus_test` wrapper
- `litmus/data/models.py`: `TestStep.instrument_arrays` field
- `litmus/execution/harness.py`: `step()` propagates arrays from logger to TestStep

## Operator UI Architecture

The UI combines NiceGUI (for browser UI) with FastAPI (for JSON API):

```
Browser ──WebSocket──► NiceGUI Pages (/, /launch, /results, /live/{id})
                              │
CLI/MCP/External ──HTTP──► FastAPI Routes (/api/runs, /api/runs/{id})
                              │
                       ┌──────┴──────┐
                       │   Shared    │
                       │   Backend   │
                       │  (Parquet)  │
                       └─────────────┘
```

- **NiceGUI pages** (`litmus/ui/app.py`): Reactive UI with left sidebar navigation
- **FastAPI routes** (`litmus/api/app.py`): JSON API at `/api/*` for programmatic access
- **Static assets** (`litmus/ui/static/`): global.css for custom styles
- **Test runner** (`litmus/execution/runner.py`): Async subprocess execution with streaming

## AI Integration Notes

The platform exposes tools for AI agents but never calls LLMs itself:
- **MCP Server:** Tools for reading context, validating config, saving files, running tests
- **HTTP API:** Mirrors MCP exactly for non-MCP clients
- **Skills:** Plain markdown documents in `litmus/skills/` directory
