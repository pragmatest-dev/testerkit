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
│   │   ├── models.py          # Pydantic models (Limit, Specification, StationConfig, etc.)
│   │   └── loader.py          # YAML loading and resolution
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
- `Capability` - Instrument capability with direction, domain, performance specs

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
- **YAML config**: `instruments/`, `stations/`, `products/`, `fixtures/`, `sequences/`
- **Python code**: `drivers/`, `tests/`

### Instrument Configuration
Instruments are defined in two layers:
1. **Instrument files** (`instruments/*.yaml`) - Per-asset identity + calibration, travels with the instrument
2. **Station files** (`stations/*.yaml`) - Role assignments + resource addresses at a specific station

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
