# Litmus Documentation

Litmus is a Python-native hardware test platform for the AI-assisted era.

## Guides

| Guide | Description |
|-------|-------------|
| [Quick Start](quickstart.md) | Install, run demo, write first test |
| [Core Concepts](concepts.md) | Products, stations, fixtures, capabilities, matching |
| [Configuration](configuration.md) | YAML schemas and Pydantic models |
| [pytest Plugin](pytest-plugin.md) | `@litmus_test`, vectors, retries |
| [Custom Drivers](instruments/custom-drivers.md) | Non-VISA, DAQmx, serial protocols |
| [Python Client](client.md) | Submit results from external tools |
| [API Reference](api.md) | MCP tools and HTTP endpoints |

## Diagrams

| Diagram | Description |
|---------|-------------|
| [Architecture ERD](architecture-erd.md) | System overview, execution flow, types vs instances |
| [Models ERD](models-erd.md) | Pydantic model relationships |

## Quick Links

**Run the demo:**
```bash
cd demo && python run_demo.py
```

**Start the UI:**
```bash
litmus serve
```

**Configure for Claude Code:**
```bash
litmus setup claude-code
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Layer                            │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │ pytest  │  │   CLI   │  │   UI    │  │   MCP   │        │
│  │  tests  │  │         │  │(NiceGUI)│  │ Server  │        │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘        │
└───────┼────────────┼────────────┼────────────┼──────────────┘
        │            │            │            │
        ▼            ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Platform Layer                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Config    │  │ Instruments │  │  Matching   │         │
│  │   Loader    │  │   Drivers   │  │   Service   │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Product   │  │   Station   │  │    Data     │         │
│  │    Specs    │  │   Configs   │  │   Backend   │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                      Storage Layer                           │
│                    Parquet Files                             │
│            (test_runs, vectors, measurements)                │
└─────────────────────────────────────────────────────────────┘
```

## Key Features

- **pytest integration** — Use familiar pytest patterns with hardware
- **Config-driven** — YAML configuration, Pydantic validation
- **Capability matching** — Automatically match products to compatible stations
- **Simulated mode** — Develop without hardware
- **AI-ready** — MCP server for Claude Code, Cursor, Cline
- **Parquet storage** — Efficient columnar storage for analytics

## Project Structure

```
litmus/
├── config/          # Configuration models and loaders
├── instruments/     # Instrument drivers and library
├── matching/        # Capability matching service
├── execution/       # pytest plugin
├── data/            # Result models and Parquet backend
├── mcp/             # MCP server
├── api/             # HTTP API (FastAPI)
├── ui/              # Operator UI (NiceGUI)
└── client.py        # Python client library
```
