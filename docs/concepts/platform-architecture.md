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
│  │ • Sequences   │  │ • Simulation  │  │               │              │
│  └───────────────┘  └───────────────┘  └───────────────┘              │
│                                                                         │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐              │
│  │    Results    │  │   Dialogs     │  │     Data      │              │
│  │    Service    │  │   Service     │  │    Models     │              │
│  │               │  │               │  │               │              │
│  │ • Parquet     │  │ • Operator    │  │ • Measurement │              │
│  │ • PostgreSQL  │  │   prompts     │  │ • TestRun     │              │
│  │ • InfluxDB    │  │ • Confirmations│ │ • Outcome     │              │
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
    │ @litmus_test│    │ (migration) │    │ Results API │
    └─────────────┘    └─────────────┘    └─────────────┘
```

## What Litmus Does NOT Provide

Litmus **does not** include a test execution engine. Instead, it integrates with existing runners:

- **pytest** — Primary integration via pytest plugin
- **OpenHTF** — Migration adapter for existing test suites
- **Custom runners** — Results API for any test source

## Multiple Entry Points

Because Litmus is a platform, you can access it through multiple entry points:

| Entry Point | Use Case | How It Works |
|-------------|----------|--------------|
| **pytest** | New test development | `@litmus_test` decorator + fixtures |
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
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, psu, dmm):
    psu.set_voltage(vector.get("vin", 5.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

The plugin provides:
- `@litmus_test` decorator for test functions
- `vector` fixture for test parameters
- Instrument fixtures from station config
- Automatic result logging

## Migration Path (OpenHTF Adapter)

For teams with existing OpenHTF test suites:

```python
from litmus.integration.openhtf import LitmusExecutor

# Use Litmus with existing OpenHTF test
with LitmusExecutor(station="bench_1", dut_serial="SN123") as executor:
    executor.execute(my_openhtf_test)
```

The adapter:
- Uses Litmus instruments and configuration
- Stores results in Litmus format
- Preserves existing test logic

## Catch-All (Results API)

For any test source (LabVIEW, TestStand, custom scripts):

```python
from litmus import LitmusClient

client = LitmusClient()

# Record results from any source
run = client.create_run(
    dut_serial="SN123",
    station_id="bench_1",
    test_sequence_id="external_test",
)

client.log_measurement(
    run_id=run.id,
    name="output_voltage",
    value=3.31,
    units="V",
    low_limit=3.135,
    high_limit=3.465,
)

client.complete_run(run.id)
```

## AI Integration (MCP)

Litmus exposes its platform services via MCP (Model Context Protocol):

```
AI Agent (Claude Code)
        │
        ▼
┌───────────────────────────────────────┐
│           MCP Server                   │
├───────────────────────────────────────┤
│ Tools:                                │
│ • litmus (CRUD operations)           │
│ • litmus_discover (find instruments) │
│ • litmus_match (capability check)    │
│ • litmus_run (execute tests)         │
│ • litmus_open (browser URLs)         │
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
- Choose your storage backend (Parquet, PostgreSQL)
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
| New test project | pytest with @litmus_test |
| Existing pytest tests | Add @litmus_test gradually |
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
                    │  Parquet │ PostgreSQL │ InfluxDB│
                    └─────────────────────────────────┘
```

Litmus is the infrastructure layer that connects your tests (top) to your data (bottom), regardless of how you choose to run them.
