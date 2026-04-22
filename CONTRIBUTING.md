# Contributing to Litmus

This guide is for developers who want to contribute to Litmus itself. It provides a deep-dive into the architecture, key abstractions, and how the major systems interact.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Core Abstractions](#core-abstractions)
3. [Data Flow](#data-flow)
4. [Module Guide](#module-guide)
5. [Extension Points](#extension-points)
6. [Development Workflow](#development-workflow)

---

## Architecture Overview

Litmus is a **hardware test platform** organized into distinct subsystems:

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Test Execution                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │
│  │   pytest    │───▶│   plugin    │───▶│  @litmus_test decorator │  │
│  │             │    │  (fixtures) │    │  + TestHarness          │  │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘  │
│         │                  │                        │                │
│         ▼                  ▼                        ▼                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │
│  │  Instruments│◀───│   Config    │───▶│   Data Models           │  │
│  │  (drivers)  │    │  (YAML +    │    │   (Measurement,         │  │
│  │             │    │   Pydantic) │    │    TestRun, etc.)       │  │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘  │
│                                                     │                │
│                                                     ▼                │
│                                        ┌─────────────────────────┐  │
│                                        │   Storage Backend       │  │
│                                        │   (Parquet files)       │  │
│                                        └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          AI Integration                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │
│  │ MCP Server  │    │  HTTP API   │    │   Skills (prompts)      │  │
│  │  (tools)    │    │  (FastAPI)  │    │                         │  │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          Operator UI                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │
│  │  NiceGUI    │    │  Dashboard  │    │   Results Viewer        │  │
│  │  (pages)    │    │  + Launch   │    │                         │  │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Two Abstraction Levels**: Users get the simple `@litmus_test` + `context` API. Test architects can use `TestHarness` directly for full control.

2. **Configuration-Driven**: Test behavior (vectors, limits, retries) lives in YAML, not code. This enables non-developers to modify tests.

3. **Hierarchical Context**: Data flows through Run → Step → Vector scopes with inheritance.

4. **AI-Ready, Not AI-Dependent**: We expose MCP tools and HTTP APIs for external agents, but the platform never calls LLMs itself.

---

## Core Abstractions

### The Context Hierarchy

The `Context` class (`litmus/execution/harness.py`) is the user-facing API for test functions. It provides scoped inheritance:

```
Run Context          (session-wide metadata)
    │
    └── Step Context     (per-test function)
            │
            └── Vector Context   (per-parameter-set)
```

**Key methods:**
```python
class Context:
    # Configure inputs (become in_* columns in Parquet)
    def configure(key: str, value: Any) -> None
    def set_in(key: str, value: Any) -> None
    def get_param(key: str, default=None) -> Any

    # Record observations (become out_* columns)
    def observe(key: str, value: Any) -> None
    def set_out(key: str, value: Any) -> None
    def get_observation(key: str, default=None) -> Any

    # Change detection for optimized loops
    def changed(key: str) -> bool

    # Access resolved limits
    def get_limit(name: str) -> Limit | None

    # Properties for bulk access
    @property inputs -> dict[str, Any]   # Merged with parent chain
    @property outputs -> dict[str, Any]  # Merged with parent chain
```

**Parent chain lookup**: When you call `context.get_param("temperature")`, it searches the current context first, then walks up the parent chain (vector → step → run) until it finds a value.

### Vector vs Context

Test vectors are defined in config and drive test looping. They are a subset of the context a test receives.

- **Vector** (`litmus/execution/vectors.py`): A dict subclass representing one parameter set from config. The harness expands and iterates over these internally.
- **Context** (`litmus/execution/harness.py`): The user-facing API that test functions receive. Contains vector params plus inherited run/step data, observations, and access to limits.

When using `@litmus_test`, you get a fully-populated `Context`. When using `TestHarness` directly, you iterate `Vector` objects and must use `run_vector()` to construct each vector-level context (which auto-populates vector params into it).

### TestHarness

The `TestHarness` class (`litmus/execution/harness.py`) is the core orchestration engine. It:

1. **Expands vectors** from config (product, zip, nested, range)
2. **Manages iteration** with `changed()` tracking across vectors
3. **Handles retries** at the vector level
4. **Resolves limits** from config, spec references, or callables
5. **Records measurements** with automatic limit checking
6. **Manages mock configuration** per-vector

**Key methods:**
```python
class TestHarness:
    # Properties
    @property vectors -> list[Vector]        # Expanded vectors
    @property context -> Context             # Current active context
    @property current_vector -> Vector|None  # During iteration

    # Vector execution
    @contextmanager
    def run_vector(vector: Vector) -> Iterator[TestVector]

    def run_with_retry(vector: Vector, test_fn: Callable) -> TestVector
    def run_all(test_fn: Callable, step_name: str) -> TestStep

    # Measurement
    def measure(name: str, value: float, limit: Limit = None) -> Measurement

    # Limit resolution (internal, but accessible via context.get_limit)
    def _resolve_limit(name: str) -> Limit | None

    # Prompts
    def prompt(message: str, prompt_type: str = "confirm") -> Any
```

### The @litmus_test Decorator

The `@litmus_test` decorator (`litmus/execution/decorators.py`) wraps test functions to:

1. Create a `TestHarness` from config (inline or YAML file)
2. Iterate over all vectors
3. Inject the `context` as first parameter (or via kwargs)
4. Handle retries
5. Record measurements from return values

**Config resolution order:**
1. Inline `config={}` parameter (highest precedence)
2. `config_file=` parameter (explicit path)
3. Auto-discovered `config.yaml` in test file's directory

**Return value handling:**
- Single value → logged as measurement with function name
- Dict → each key-value logged as separate measurement
- Tuple `(name, value)` → single named measurement
- Generator → streamed measurements

### Data Models

The result hierarchy (`litmus/data/models.py`):

```
TestRun
├── id, started_at, ended_at
├── dut: DUT (serial, part_number, revision)
├── station_id, operator_id, etc.
├── outcome: Outcome (PASS/FAIL/ERROR/SKIP)
└── steps: list[TestStep]
        ├── name, description
        ├── outcome
        └── vectors: list[TestVector]
                ├── index, params (in_*)
                ├── observations (out_*)
                ├── outcome
                └── measurements: list[Measurement]
                        ├── name, value, units
                        ├── low_limit, high_limit, nominal
                        ├── outcome
                        └── spec_ref (traceability)
```

**Key model: `Measurement`**
```python
class Measurement:
    name: str
    value: float | None
    units: str | None
    low_limit: float | None
    high_limit: float | None
    nominal: float | None
    outcome: Outcome | None
    spec_ref: str | None      # Human-readable spec reference
    dut_pin: str | None       # DUT pin measured
    instrument_channel: str | None  # Instrument channel used

    def check_limit() -> Outcome  # Evaluates value against limits
```

### Limit Resolution

Limits can come from multiple sources. Resolution order in `TestHarness._resolve_limit()`:

1. **Direct Limit object** in config
2. **MeasurementLimitConfig** with direct values (low/high/nominal)
3. **Spec reference** → resolves via `SpecContext`
4. **Callable** → Python function or inline code evaluated with context
5. **SpecContext lookup** → characteristic name matches measurement name

**Callable limits** enable dynamic limits based on current vector:
```yaml
limits:
  output_voltage:
    callable: "Limit(low=ctx.get_param('vin') * 0.65, high=ctx.get_param('vin') * 0.68, units='V')"
```

### SpecContext (Spec-Driven Testing)

`SpecContext` (`litmus/products/context.py`) bridges product specifications and test execution:

```python
class SpecContext:
    product: Product              # Loaded product spec
    fixture: FixtureConfig|None   # Fixture routing
    default_guardband_pct: float  # Default tightening

    def get_limit(char_id: str, guardband_pct=None, **conditions) -> Limit
    def get_characteristic(char_id: str) -> Characteristic
    def get_pin_info(char_id: str) -> dict  # For traceability
```

**Limit derivation** handles:
- Condition matching (temperature, load, etc.)
- Guardband application (tightens limits by %)
- Spec reference generation for traceability

---

## Data Flow

### Test Execution Flow

```
1. pytest starts
   └── pytest_configure() registers markers

2. Session starts
   ├── logger fixture creates TestRunLogger
   ├── instruments fixture connects to hardware (or mocks)
   └── spec_context fixture loads product spec

3. Each test function
   ├── @litmus_test decorator takes over
   │   ├── Creates TestHarness with config
   │   ├── Expands vectors from config
   │   └── For each vector:
   │       ├── run_vector() creates Context
   │       ├── Injects context into test function
   │       ├── Test runs, returns value(s)
   │       ├── _record_result() creates Measurements
   │       └── Measurements checked against limits
   │
   └── Results accumulated in TestStep

4. Session ends
   ├── logger.finalize() completes TestRun
   └── ParquetBackend.save_test_run() writes results
```

### Measurement Recording Flow

```
Test function returns value(s)
        │
        ▼
TestHarness._record_result()
        │
        ├── dict → multiple measurements
        ├── tuple → named measurement
        └── value → measurement with step name
                │
                ▼
        TestHarness.measure()
                │
                ├── Resolve limit (_resolve_limit)
                ├── Create Measurement object
                ├── measurement.check_limit()
                └── Append to current TestVector
```

### Context Inheritance Flow

```
Context created for vector
        │
        ├── Parent = step context (or run context)
        ├── Prev = previous vector context (for changed())
        └── Harness = TestHarness reference (for get_limit())
                │
                ▼
        Vector params → context._inputs
                │
                ▼
        context.get_param("key") checks:
        1. This context._inputs
        2. Parent context._inputs (recursive)
        3. Return default
```

---

## Module Guide

### litmus/execution/

The core test execution engine.

| File | Purpose |
|------|---------|
| `plugin.py` | pytest plugin - fixtures, hooks, CLI options |
| `harness.py` | TestHarness + Context classes |
| `vectors.py` | Vector expansion (product, zip, nested, range) |
| `decorators.py` | @litmus_test, @measure, @litmus_step decorators |
| `logger.py` | TestRunLogger for accumulating results |
| `runner.py` | Async subprocess runner for UI |

**Entry point**: `plugin.py` registers with pytest and provides fixtures that create harnesses and loggers.

### litmus/config/

Configuration models and loading.

| File | Purpose |
|------|---------|
| `models.py` | Pydantic models: Limit, Specification, RetryConfig, etc. |
| `loader.py` | YAML loading and test config resolution |

**Key models**:
- `Limit` - Test limit with units and spec reference
- `MeasurementLimitConfig` - Flexible limit configuration (direct, ref, callable)
- `RetryConfig` - Retry behavior settings
- `VectorConfig` - Vector expansion configuration

### litmus/data/

Data models and storage backends.

| File | Purpose |
|------|---------|
| `models.py` | TestRun, TestStep, TestVector, Measurement, Outcome |
| `backends/parquet.py` | Parquet file storage |

**Parquet schema**: Results are flattened to rows per measurement with `in_*` and `out_*` columns for context.

### litmus/instruments/

Instrument drivers and mocks.

| File | Purpose |
|------|---------|
| `base.py` | Abstract Instrument base class |
| `visa.py` | VisaInstrument for SCPI instruments |
| `dmm.py`, `psu.py`, `eload.py`, `scope.py` | Concrete drivers |
| `mocks.py` | Generic Mock factory |

**Mock system**: `Mock(DMM, measure_voltage=3.3)` creates a mock that inherits from DMM, passes isinstance checks, and returns configured values.

### litmus/products/

Product specification system.

| File | Purpose |
|------|---------|
| `models.py` | Product, Characteristic, Pin, TestRequirement |
| `context.py` | SpecContext for spec-driven testing |
| `loader.py` | YAML loading for product specs |
| `limits.py` | derive_limit() function |

### litmus/mcp/

MCP server for AI integration.

| File | Purpose |
|------|---------|
| `server.py` | FastMCP server definition |
| `tools.py` | Tool implementations (litmus, discover, match, run, open) |

### litmus/ui/

NiceGUI operator interface.

| Directory | Purpose |
|-----------|---------|
| `pages/` | Dashboard, launch, results, live views |
| `shared/` | Layout, components, dialogs |
| `static/` | CSS assets |

### litmus/api/

HTTP API endpoints.

| File | Purpose |
|------|---------|
| `app.py` | FastAPI + NiceGUI app factory |
| `models.py` | API request/response models |

---

## Extension Points

### Adding a New Instrument Driver

1. Create `litmus/instruments/new_instrument.py`:
```python
from litmus.instruments.visa import VisaInstrument

class NewInstrument(VisaInstrument):
    def measure_something(self) -> float:
        return float(self.query("MEAS:SOMETHING?"))

    def set_something(self, value: float) -> None:
        self.write(f"SOMETHING {value}")
```

2. Register SCPI mapping for mocks in `mocks.py`:
```python
_register_scpi_mapping(
    NewInstrument,
    {
        "measure_something": ["MEAS:SOMETHING?"],
        "something": ["MEAS:SOMETHING?"],  # Alias
    },
)
```

3. Add to driver lookup in `plugin.py`:
```python
def _get_driver_class(instrument_type: str):
    from litmus.instruments import NewInstrument
    drivers = {
        # ...
        "new_instrument": NewInstrument,
    }
```

### Adding a New Vector Expansion Mode

1. Add expansion function in `vectors.py`:
```python
def expand_custom(config: dict) -> list[Vector]:
    # Your expansion logic
    result = []
    for i, params in enumerate(your_expansion):
        v = Vector(params)
        v["_index"] = i
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result
```

2. Register in `expand_vectors()`:
```python
if expand_mode == "custom":
    return expand_custom(config)
```

### Adding a New Storage Backend

1. Create `litmus/data/backends/new_backend.py`:
```python
class NewBackend:
    def save_test_run(self, test_run: TestRun) -> str:
        # Save and return ID/path
        pass

    def load_test_run(self, run_id: str) -> TestRun:
        pass

    def list_runs(self, limit: int = 100) -> list[dict]:
        pass
```

2. Use your backend in plugin.py or configure it via settings.

### Adding MCP Tools

1. Add tool implementation in `mcp/tools.py`:
```python
def new_tool_impl(arg1: str, arg2: int) -> dict[str, Any]:
    # Implementation
    return {"result": "..."}
```

2. Register in `mcp/server.py`:
```python
@mcp.tool(name="litmus_new")
def new_tool(arg1: str, arg2: int) -> dict[str, Any]:
    """Tool description for AI agents."""
    return new_tool_impl(arg1, arg2)
```

---

## Development Workflow

### Setup

```bash
# Clone and install with all optional extras (pyright needs these installed
# to resolve imports for the exporters, transports, grafana, etc.)
git clone <repo>
cd litmus
uv sync --all-extras

# Install the pre-commit hooks once per clone. Hooks run ruff check,
# ruff format, pyright, and a handful of safety checks on every commit.
uv run pre-commit install

# Run tests
pytest

# Run with coverage
pytest --cov=litmus

# Lint, format, type-check (all run by the pre-commit hook too)
uv run ruff check .
uv run ruff format .
uv run pyright

# Run all pre-commit hooks manually across the repo
uv run pre-commit run --all-files
```

### Testing Your Changes

**Unit tests**: Add to `tests/` directory
```bash
pytest tests/test_your_feature.py -v
```

**Demo tests** (with mock instruments):
```bash
cd demo
pytest tests/ --station=demo_station_001 --mock-instruments -v
```

**Integration testing**:
```bash
# Start UI
litmus serve --reload

# Run MCP server
litmus mcp serve
```

### Code Style Guidelines

1. **Pydantic for config/data models**: All configuration and result structures use Pydantic
2. **Type hints everywhere**: Use type annotations, especially for public APIs
3. **Docstrings**: Google style, with Args/Returns/Raises sections
4. **No magic**: Prefer explicit over implicit. Configuration should be visible.
5. **YAML for config**: Human-editable configuration stays in YAML files

### Common Patterns

**Context manager for resources**:
```python
with harness.run_vector(vector) as tv:
    # Vector execution
    harness.measure("name", value)
```

**Limit resolution with fallback**:
```python
limit = context.get_limit("measurement_name")
if limit:
    # Use limit
else:
    # No limit configured
```

**Mock value configuration**:
```python
# Per-vector in config.yaml
vectors:
  - vin: 5.0
    _mocks:
      dmm.measure_voltage: 3.3
```

### Debugging Tips

1. **Check vector expansion**: Print `harness.vectors` to see expanded params
2. **Trace limit resolution**: Add logging to `_resolve_limit()`
3. **Mock behavior**: Check `mock.mock_write_log` for SCPI commands sent
4. **Context inheritance**: Print `context.params` at each level

---

## Questions?

- Check existing tests in `tests/` for usage examples
- The `demo/` directory has complete working examples
- Open an issue for design questions
