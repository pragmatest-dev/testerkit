# Platform Architecture

Litmus is a **hardware test platform**, not a test framework. Understanding this distinction is key to using Litmus effectively.

## Platform vs Framework

| | Framework | Platform |
|---|-----------|----------|
| **Scope** | Runs tests | Provides infrastructure |
| **Test execution** | Framework does it | Delegates to pytest/OpenHTF/etc. |
| **Entry points** | One (the framework) | Many (CLI, API, MCP, UI) |
| **Extensibility** | Plugins | Modular services |
| **Examples** | pytest, Robot Framework | Litmus, NI TestStand |

## What Litmus Provides

Litmus provides **infrastructure services** that any test runner can use:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         LITMUS PLATFORM                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐              │
│  │ Configuration │  │  Instruments  │  │   Matching    │              │
│  │    Service    │  │    Service    │  │    Service    │              │
│  │               │  │               │  │               │              │
│  │ • Products    │  │ • DMM, PSU    │  │ • Capabilities│              │
│  │ • Stations    │  │ • Scope       │  │ • Compatibility│             │
│  │ • Fixtures    │  │ • ELoad       │  │ • Requirements│              │
│  │               │  │ • Simulation  │  │               │              │
│  └───────────────┘  └───────────────┘  └───────────────┘              │
│                                                                         │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐              │
│  │  Event Log   │  │   Dialogs     │  │   Channels    │              │
│  │   Service    │  │   Service     │  │    Service    │              │
│  │               │  │               │  │               │              │
│  │ • EventStore │  │ • Operator    │  │ • ChannelStore│              │
│  │ • Parquet    │  │   prompts     │  │ • Flight RPC  │              │
│  │ • DuckDB     │  │ • Confirmations│ │ • LTTB decim. │              │
│  └───────────────┘  └───────────────┘  └───────────────┘              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           │                  │                  │
           ▼                  ▼                  ▼
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │   pytest    │    │   OpenHTF   │    │  Your Own   │
    │   plugin    │    │   adapter   │    │   runner    │
    │             │    │             │    │             │
    │ native      │    │ (migration) │    │ Results API │
    │ fixtures    │    │             │    │             │
    └─────────────┘    └─────────────┘    └─────────────┘
```

## What Litmus Does NOT Provide

Litmus **does not** include a test execution engine. Instead, it integrates with existing runners:

- **pytest** — Primary integration via pytest plugin
- **OpenHTF** — Migration adapter for existing test suites (OpenHTF is Google's open-source hardware-test framework)
- **Custom runners** — Results API for any test source

## Multiple Entry Points

Because Litmus is a platform, you can access it through multiple entry points:

| Entry Point | Use Case | How It Works |
|-------------|----------|--------------|
| **pytest** | New test development | pytest-native: `context`, `verify`, `logger` fixtures |
| **CLI** | Operations, debugging | `litmus runs`, `litmus show` |
| **HTTP API** | CI/CD, dashboards | `POST /api/runs`, `GET /api/runs/{id}` |
| **MCP Server** | AI integration | Claude Code, other AI agents |
| **Operator UI** | Production floor | NiceGUI web interface |

All entry points share the same:
- Configuration files
- Instrument drivers
- Result storage
- Data models

## pytest Integration (Primary Path)

For new projects, use the pytest plugin:

```python
def test_output_voltage(context, psu, dmm, logger):
    psu.set_voltage(context.get_param("vin", 5.0))
    psu.enable_output()
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

The plugin provides:
- `context`, `verify`, and `logger` fixtures
- Instrument fixtures from station config
- Automatic result logging via `logger.measure` / `verify`

## Catch-All (Results API)

For any test source (LabVIEW, TestStand, custom scripts):

```python
from litmus.client import LitmusClient

client = LitmusClient()

run = client.start_run(
    dut_serial="SN123",
    station_id="bench_1",
    test_phase="production",
)

with run.step("output_voltage") as step:
    step.measure("output_voltage", 3.31, units="V", low=3.135, high=3.465)

run.finish()
```

See the [Python client reference](../reference/client.md) for the full surface (`start_run`, `RunBuilder.step`, `StepBuilder.measure`, `VectorBuilder` for parametrized steps).

## AI Integration (MCP)

Litmus exposes its platform services via MCP (Model Context Protocol):

```
AI Agent (Claude Code)
        │
        ▼
┌───────────────────────────────────────┐
│           MCP Server                   │
├───────────────────────────────────────┤
│ Tools (twelve, all `litmus_*`):        │
│ • litmus_project (CRUD on YAML)        │
│ • litmus_discover (find instruments)   │
│ • litmus_match (capability check)      │
│ • litmus_run (execute tests)           │
│ • litmus_open (browser URLs)           │
│ • litmus_schema (entity JSON schema)   │
│ • litmus_events (query events)         │
│ • litmus_sessions (list sessions)      │
│ • litmus_channels (query channels)     │
│ • litmus_metrics (yield / Pareto / Cpk)│
│ • litmus_runs (query runs view)        │
│ • litmus_steps (query steps view)      │
└───────────────────────────────────────┘
        │
        ▼
  Litmus Platform Services
```

**Important:** Litmus does NOT call LLMs. It exposes tools for AI agents to call.

## Benefits of Platform Architecture

### 1. Separation of Concerns

- Configuration is separate from test code
- Instrument drivers are reusable
- Results are stored consistently

### 2. Flexibility

- Choose your test runner (pytest, OpenHTF, custom)
- Storage: Event log (Arrow IPC) + Parquet (materialized views) + Channels (time-series)
- Choose your integration (CLI, API, UI, AI)

### 3. Incremental Adoption

Start small, expand as needed:

1. **Phase 1:** Use Results API to store test data
2. **Phase 2:** Add configuration management
3. **Phase 3:** Add instrument drivers
4. **Phase 4:** Add AI tooling

### 4. Team Scalability

- Developers write test code (pytest)
- Engineers configure limits (YAML)
- Operators run tests (UI)
- CI/CD monitors results (API)
- AI assists with test generation (MCP)

## Comparison with Other Systems

| System | Type | Litmus Equivalent |
|--------|------|-------------------|
| pytest | Test framework | pytest plugin (primary integration) |
| Robot Framework | Test framework | Could build integration |
| NI TestStand | Test platform | Similar concept, different tech |
| OpenHTF | Test framework | Migration adapter available |

## When to Use What

| Scenario | Recommended Approach |
|----------|---------------------|
| New test project | pytest-native tests with `context`/`verify`/`logger` fixtures |
| Existing pytest tests | Drop in Litmus fixtures + sidecar YAML incrementally |
| Existing OpenHTF tests | Use OpenHTF adapter |
| LabVIEW/TestStand tests | Use Results API |
| AI-assisted development | Use MCP server |

## Architecture Summary

```
                    ┌─────────────────────────────────┐
                    │         USER INTERFACES         │
                    │                                 │
                    │  CLI   API   MCP   UI   pytest │
                    └─────────────┬───────────────────┘
                                  │
                    ┌─────────────▼───────────────────┐
                    │       LITMUS PLATFORM           │
                    │                                 │
                    │  Config │ Instruments │ Matching│
                    │  ───────┼─────────────┼─────────│
                    │  Results│   Dialogs   │ Products│
                    └─────────────┬───────────────────┘
                                  │
                    ┌─────────────▼───────────────────┐
                    │         STORAGE LAYER           │
                    │                                 │
                    │  Events  │ Channels │  Parquet  │
                    │ (Arrow)  │ (Arrow)  │ (results) │
                    └─────────────────────────────────┘
```

Litmus is the infrastructure layer that connects your tests (top) to your data (bottom), regardless of how you choose to run them.
