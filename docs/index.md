# Litmus Documentation

Litmus is a Python-native hardware test platform for the AI-assisted era.

## Documentation Sections

| Section | Description |
|---------|-------------|
| [Tutorial](tutorial/index.md) | Engineer's First Project - progressive learning path |
| [Integration](integration/overview.md) | Adopt Litmus with existing tests and infrastructure |
| [Concepts](concepts/overview.md) | Products, stations, capabilities, fixtures, and matching |
| [How-To Guides](guides/writing-tests.md) | Step-by-step guides for common tasks |
| [Reference](reference/api.md) | MCP tools, HTTP endpoints, CLI, models |
| [Examples](examples/power-converter.md) | Complete working examples |

## Quick Start

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

## Architecture Overview

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
│   Events (Arrow IPC)  │  Channels (Arrow)  │  Parquet       │
│   DuckDB via Flight   │  LTTB decimation   │  (results)     │
└─────────────────────────────────────────────────────────────┘
```

## Key Features

- **pytest integration** — Use familiar pytest patterns with hardware
- **Config-driven** — YAML configuration, Pydantic validation
- **Capability matching** — Automatically match products to compatible stations
- **Simulated mode** — Develop without hardware
- **AI-ready** — MCP server for Claude Code, Cursor, Cline
- **Event log** — Typed event stream with Arrow IPC storage and DuckDB queries
- **Channel store** — Time-series instrument data with LTTB decimation
- **Parquet storage** — Efficient columnar storage for analytics
- **Live monitoring** — Real-time event subscriptions via Arrow Flight

## Learning Paths

### New to Litmus?

Start with the [Tutorial](tutorial/index.md) — a progressive learning path from your first test to production deployment.

### Have Existing Tests?

Check out [Integration](integration/overview.md) — guides for adopting Litmus incrementally with LabVIEW, TestStand, or existing pytest suites.

### Quick Reference

Jump to [Reference](reference/api.md) for API documentation, configuration schemas, and CLI commands.

## Project Structure

```
litmus/
├── config/          # Configuration models and loaders
├── instruments/     # Instrument drivers and library
├── matching/        # Capability matching service
├── execution/       # pytest plugin
├── data/            # Event log, channels, Parquet backend
├── mcp/             # MCP server
├── api/             # HTTP API (FastAPI)
├── ui/              # Operator UI (NiceGUI)
└── client.py        # Python client library
```

## Getting Help

- **GitHub Issues:** [Report bugs and request features](https://github.com/anthropics/litmus/issues)
- **CLI Help:** `litmus --help`
