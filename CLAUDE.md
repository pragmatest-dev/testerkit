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
- **Web Framework:** FastAPI
- **Operator UI:** HTMX
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
│   ├── capabilities/          # Instrument capability models
│   │   ├── models.py          # Capability, Direction, Domain enums
│   │   └── features.py        # Standard feature vocabulary
│   ├── instruments/           # Instrument drivers
│   │   ├── base.py            # Base instrument class
│   │   ├── library/           # Instrument definition YAML files
│   │   └── drivers/           # Protocol-specific drivers
│   ├── execution/             # Test execution engine
│   │   ├── plugin.py          # pytest plugin
│   │   └── fixtures.py        # pytest fixtures
│   ├── data/                  # Data/logging service
│   │   ├── models.py          # Result models
│   │   └── backends/          # Storage backends
│   ├── mcp/                   # MCP server
│   │   └── server.py          # MCP tool definitions
│   ├── api/                   # HTTP API
│   │   └── routes.py          # FastAPI routes
│   └── ui/                    # Operator UI
│       └── app.py             # HTMX-based web UI
├── stations/                  # Station configurations
│   ├── _base.yaml             # Base station type definitions
│   └── station_*.yaml         # Instance configurations
├── fixtures/                  # Test fixture definitions
├── specs/                     # Product specifications
├── tests/                     # Test suites
│   ├── conftest.py            # pytest configuration
│   └── test_*/                # Product-specific tests
├── skills/                    # AI skills (portable markdown docs)
│   ├── SKILL.md
│   ├── recipes/               # YAML workflows
│   └── prompts/               # Sub-agent prompts
├── results/                   # Parquet output (gitignored)
├── litmus.yaml                # Project-level configuration
├── pyproject.toml             # Python project configuration
└── litmus-architecture.md     # Architecture specification
```

## Key Pydantic Models

- `Limit` - Test limit with units and spec reference
- `Specification` - Product specification that limits derive from
- `InstrumentConfig` / `InstrumentInstance` - Instrument configuration
- `StationType` / `StationInstance` - Station templates and instances
- `FixtureConfig` / `FixtureChannel` - Test fixture definitions
- `DialogConfig` - Operator dialog definitions
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

# MCP server
litmus mcp serve               # Start MCP server

# Setup for AI tools
litmus setup claude-code       # Configure for Claude Code
litmus setup cursor            # Configure for Cursor
```

## Configuration Patterns

### Station Configuration
Stations are defined in two layers:
1. **Station Types** (`stations/_base.yaml`) - Abstract templates with instrument requirements
2. **Station Instances** (`stations/station_*.yaml`) - Concrete deployments with VISA addresses

### Test Configuration
Tests use YAML configs (`tests/test_*/config.yaml`) that define:
- Test sequences with steps
- Operator dialogs
- Limit references to specs
- Retry behavior

### Specs to Limits
Product specifications (`specs/`) define nominal values and tolerances. Test limits are derived from specs with optional guardbanding:
```python
spec.to_limit(guardband_pct=Decimal("10"))
```

## Development Guidelines

- Use Pydantic models for all configuration and data structures
- Prefer YAML for human-editable configuration files
- Keep instrument drivers thin—use PyVISA for SCPI instruments
- All MCP tools should have equivalent HTTP API endpoints
- Results use a consistent schema across all storage backends
- Non-blocking operator dialogs via FastAPI + HTMX

## Testing Approach

- Use pytest as the test runner
- Custom pytest plugin provides fixtures for instruments, config, and dialogs
- Test configuration is separate from test code
- Support for retry logic, skip conditions, and operator prompts

## AI Integration Notes

The platform exposes tools for AI agents but never calls LLMs itself:
- **MCP Server:** Tools for reading context, validating config, saving files, running tests
- **HTTP API:** Mirrors MCP exactly for non-MCP clients
- **Skills:** Plain markdown documents in `skills/` directory
