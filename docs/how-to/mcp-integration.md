# AI-assisted test development via MCP

Litmus exposes a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server with **12 tools** that expose the datasheet → test workflow to AI assistants. The platform does **not** call LLMs itself — it only exposes tools that an AI agent drives.

Full per-tool reference: [api.md → MCP tools](../reference/api.md#mcp-tools). This page is the operational how-to.

> **Prerequisites.** `litmus` installed and on `$PATH` (`uv pip install litmus`). One of the supported AI clients listed below — Claude Code, Claude Desktop, GitHub Copilot, Cursor, or Cline. A working project directory (`litmus init` to scaffold one). For `litmus_run`, real or mock instruments configured in `stations/`.

## Setup

`litmus setup <client>` writes the right MCP config file for each supported client. All commands accept `--print-only` to show the config that would be written without modifying anything on disk.

| Client | Command | What gets written |
|---|---|---|
| Claude Code (CLI) | `litmus setup claude-code` | `~/.claude.json` MCP server entry |
| Claude Desktop | `litmus setup claude-desktop` | `~/.config/Claude/claude_desktop_config.json` (auto-detects WSL paths) |
| GitHub Copilot Chat | `litmus setup copilot` | `~/.config/github-copilot/intellij/mcp.json` |
| Cursor | `litmus setup cursor` | `~/.cursor/mcp.json` |
| Cline (VS Code) | `litmus setup cline` | VS Code workspace MCP settings |
| Anything else | `litmus mcp serve` directly | You configure your AI client manually |

After running any setup command, restart the client to pick up the new MCP server.

To inspect the active config across all clients:

```bash
litmus setup show
```

For the manual path (any client that doesn't have a `litmus setup` subcommand), the MCP server runs over stdio:

```bash
litmus mcp serve
# command: litmus
# args: ["mcp", "serve"]
# transport: stdio (only supported transport — see cli.md)
```

Add a server entry to your AI client's MCP config pointing at the above. See `litmus setup claude-desktop --print-only` for a working example you can adapt.

## The 12 MCP tools

Defined in `src/litmus/mcp/server.py` via `@mcp.tool(name=...)`.

| Tool | Purpose | Detail |
|---|---|---|
| `litmus_project` | Unified CRUD: init, list, get, save, read | [details below](#litmus_project) |
| `litmus_discover` | Scan for connected VISA instruments | Returns the list of resources VISA can see on this host |
| `litmus_match` | Find compatible instruments and stations | Two modes: requirements (catalog recommendation) and station (compatibility check) |
| `litmus_run` | Execute a test file via pytest, return exit summary | [details below](#litmus_run) |
| `litmus_open` | Get a browser URL for the operator UI | Allowed `type`: `product`, `station`, `run`, `fixture` |
| `litmus_schema` | Get the JSON Schema for a YAML type | For AI clients that want to validate before saving |
| `litmus_events` | Query the event store | Filter by session / event type |
| `litmus_sessions` | List sessions with metadata | Each session = one `connect()` lifetime or pytest run |
| `litmus_channels` | Query channel data from the streaming store | For waveform / time-series readouts referenced by events |
| `litmus_metrics` | Compute yield / Pareto / Cpk / retest / time-loss | Aggregations over a date range |
| `litmus_runs` | Query the runs view (filtered, paginated) | Same data the operator-UI runs list reads |
| `litmus_steps` | Query the steps view (one row per step execution) | Step-level rollup with outcome and timing |

For each tool's full parameter list and return shape, see [`api.md`](../reference/api.md#mcp-tools).

### `litmus_project`

The CRUD entry point. One tool with an `action:` dispatcher; the rest of the workflow goes through it.

```python
# Initialize a project (call this first)
result = litmus_project(action="init", path="~/my-project")
project = result["project_root"]

# List entities of a type
litmus_project(action="list", type="product", project=project)

# Get one entity
litmus_project(action="get", type="product", id="tps54302", project=project)

# Save an entity (validated against schema)
litmus_project(action="save", type="product", id="tps54302",
               content={...}, project=project)

# Read a file or a template
litmus_project(action="read", path="products/tps54302.yaml", project=project)
litmus_project(action="read", path="template:test", project=project)
```

**Entity types depend on the action** (`src/litmus/mcp/tools.py:224-240`):

- `list` / `get` accept: `station`, `product`, `fixture`, `catalog`, `instrument_asset`, `run`
- `save` accepts: `station`, `product`, `fixture`, `catalog`, `instrument_asset`, `test`

`test` is save-only; `run` is read-only. `project` is not a type — it's the path argument every other call passes.

#### Saving test code with `action="save", type="test"`

When `type="test"`, the tool writes a **Python file** under `<project_root>/tests/`. The `id` is treated as the path; if it doesn't end in `.py`, the tool appends `.py` (`mcp/tools.py:608`). Files saved this way will not be loaded as sidecar YAML — for the colocated sidecar (`tests/test_<module>.yaml`), write it via `litmus_project(action="save", type="test", id="tests/test_<module>.yaml.py", ...)` is **wrong**; the sidecar shape requires a real `.yaml` file. Write sidecar YAML directly to disk (the AI client's filesystem tool, not this MCP tool).

### `litmus_run`

Spawns pytest as a subprocess and returns the parsed exit summary. **It does not return structured measurement results** — those land in the parquet store and are queried separately via `litmus_runs` / `litmus_metrics` / `litmus_steps`.

```python
result = litmus_run(
    test="tests/test_tps54302.py",
    station="bench_1",
    serial="SN001",
    project=project,
)
```

Return shape (`mcp/tools.py:1164-1173`):

```python
{
    "run_id": "abc12345...",                # UUID of the run (or "unknown")
    "status": "passed",                     # one of: "passed" | "failed" | "error"
    "summary": "1 passed in 0.42s",         # pytest's bottom-line summary
    "test": "tests/test_tps54302.py",
    "station": "bench_1",
    "serial": "SN001",
    "started_at": "2026-05-17T...",
    "output": "<last 2000 chars of pytest stdout>",
}
```

`status` is **not** an `Outcome` enum value — it's derived from `subprocess.returncode`:

| returncode | `status` |
|---|---|
| 0 | `"passed"` |
| 1 | `"failed"` |
| any other | `"error"` |

For the full 7-value `Outcome` enum (`passed`/`failed`/`errored`/`skipped`/`done`/`terminated`/`aborted`) that the runtime cascade produces, query the parquet store after the run finishes:

```python
runs = litmus_runs(filters={"run_id": result["run_id"]}, project=project)
run = runs["runs"][0]
print(run["run_outcome"])           # one of the 7 Outcome values
```

See [outcomes](../concepts/outcomes.md) for what each value means.

## The AI-driven workflow

Four steps + an init. The AI assistant drives them through MCP tools; you drive the AI assistant.

### Step 0 — Initialize the project

```python
result = litmus_project(action="init", path="~/my-hardware-tests")
project = result["project_root"]
```

Creates the project skeleton: `pyproject.toml`, `litmus.yaml`, `conftest.py`, and the entity directories (`products/`, `stations/`, `fixtures/`, `tests/`, `catalog/`).

After this, **you** (the human) need to drop to a terminal and run `uv sync` to install dependencies. The AI assistant cannot do this for you — running shell commands requires explicit user action.

### Step 1 — Create a product spec from the datasheet

A product spec declares the DUT's pins and its [characteristics](../concepts/capabilities.md) (measurable properties + their spec bands). The spec is what `verify(name, value)` resolves limits against later.

Key concepts in datasheet vocabulary:

| Litmus term | Datasheet vocabulary | What it captures |
|---|---|---|
| Characteristic | An Electrical Characteristic table row | A measurable DUT property — output voltage, quiescent current, etc. |
| SpecBand | One condition row in the EC table | A nominal value + tolerance at a specific operating point |
| `when:` clause | The "Conditions" column entry for that row | The operating point this band applies at (temperature, load, frequency) |
| `accuracy:` | The `±tol` and `(min, typ, max)` columns | How tight the band is around the nominal |

Worked example for a TPS54302 buck converter:

```python
litmus_project(action="save", type="product", id="tps54302", content={
    "id": "tps54302",
    "name": "TPS54302 3A Synchronous Buck Converter",
    "part_number": "TPS54302DSGR",
    "pins": {
        # name: physical designator on the part (J1, U3.5, TP7, Pin 1, etc.)
        "VIN":  {"name": "1", "net": "VIN",      "role": "power"},
        "VOUT": {"name": "5", "net": "VOUT_3V3", "role": "power"},
        "FB":   {"name": "3", "net": "FB",       "role": "signal"},
    },
    "characteristics": {
        "output_voltage": {
            "function": "dc_voltage",
            "direction": "output",
            "units": "V",
            "pin": "VOUT",
            "bands": [
                {"when": {"temperature": 25, "load": 0.5},
                 "value": 3.3, "accuracy": {"pct_reading": 1.5}},
                {"when": {"temperature": 25, "load": 3.0},
                 "value": 3.3, "accuracy": {"pct_reading": 2.0}},
                {"when": {"temperature": 85, "load": 3.0},
                 "value": 3.3, "accuracy": {"pct_reading": 3.0}},
            ],
        },
        "quiescent_current": {
            "function": "dc_current",
            "direction": "input",
            "units": "mA",
            "pin": "VIN",
            "bands": [
                {"when": {"temperature": 25, "load": 0},
                 "value": 5, "accuracy": {"absolute": 0.5}},
            ],
        },
    },
}, project=project)
```

For the full product schema see [configuration reference → product](../reference/configuration.md#product-specification). For the band-matching and `accuracy:` semantics see [capabilities → condition-dependent specs](../concepts/capabilities.md#condition-dependent-specs-specband).

### Step 2 — Set up the test station

The station YAML declares what instruments live on this bench and what `mock_config:` returns when running without hardware.

```python
# Optionally, ask the matcher for compatible instruments first
matches = litmus_match(
    requirements=[
        {"function": "dc_voltage", "direction": "output", "range_max": 20, "units": "V"},
        {"function": "dc_voltage", "direction": "input",  "range_max": 50, "units": "V"},
    ],
    project=project,
)

litmus_project(action="save", type="station", id="bench_1", content={
    "id": "bench_1",
    "name": "Development Bench",
    "instruments": {
        "psu": {
            "type": "psu",
            "driver": "drivers.PSU",
            "resource": "GPIB0::1::INSTR",
            "catalog_ref": "keysight_e36312a",
            "mock": True,
            "mock_config": {                # keys must be METHOD names
                "set_voltage": None,
                "measure_voltage": 12.0,
            },
        },
        "dmm": {
            "type": "dmm",
            "driver": "drivers.DMM",
            "resource": "GPIB0::5::INSTR",
            "catalog_ref": "keysight_34461a",
            "mock": True,
            "mock_config": {
                "measure_dc_voltage": 3.3,
            },
        },
    },
}, project=project)
```

`mock_config:` keys are the **method names** the driver class exposes, not signal names. With `--mock-instruments` (or `mock: true`), the platform substitutes `Mock(driver_class, **mock_config)` for the real driver, and those methods return the configured values. See [mock mode](mock-mode.md) for the full story.

### Step 3 — Generate the test files

Two files: the Python test module and its colocated sidecar YAML.

Ask for the current test template first so the AI client sees the canonical patterns:

```python
template = litmus_project(action="read", path="template:test", project=project)
```

**Test code** (`tests/test_tps54302.py`):

```python
litmus_project(
    action="save",
    type="test",
    id="tests/test_tps54302.py",
    content={"code": '''
def test_output_voltage(context, psu, dmm, verify):
    """Verify output voltage across temperature and load conditions."""
    temperature = context.get_param("temperature", 25)
    load = context.get_param("load", 0.5)
    vin = context.get_param("vin", 12.0)

    psu.set_voltage(vin)
    psu.enable_output()

    # verify() resolves the limit from the product spec at this vector
    # condition and raises LimitFailure if the value is out of band.
    verify("output_voltage", dmm.measure_dc_voltage())


def test_quiescent_current(context, psu, dmm, verify):
    psu.set_voltage(context.get_param("vin", 12.0))
    psu.enable_output()
    verify("quiescent_current", dmm.measure_dc_current())
'''},
    project=project,
)
```

The `context`, `psu`, `dmm`, `verify` test arguments are all [pytest fixtures the plugin synthesizes](../reference/litmus-fixtures.md):

- `verify(name, value)` — resolve limit from product/sidecar, record measurement row, raise on FAIL
- `context.get_param(name, default)` — read the active vector's parameter value
- `psu` / `dmm` — [per-role auto-fixtures](../reference/litmus-fixtures.md#per-role-auto-fixtures) synthesized from the station YAML

**Sidecar YAML** (`tests/test_tps54302.yaml`) — write this with your AI client's filesystem tool, not via `litmus_project(action="save", type="test")` (which forces a `.py` extension):

```yaml
tests:
  test_output_voltage:
    sweeps:
      - {temperature: [25, 85]}         # outer loop (slowest)
      - {load: [0.1, 0.5, 0.8, 3.0]}    # middle loop
      - {vin: [10.5, 12.0, 15.0]}       # inner loop (fastest)
    characteristics: [output_voltage]
    limits:
      output_voltage:
        characteristic: output_voltage  # auto-derive from product SpecBand at vector conditions
        tolerance_pct: 10               # widen to manufacturing margin
        comparator: GELE                # low <= value <= high

  test_quiescent_current:
    sweeps:
      - {temperature: [25], load: [0], vin: [12.0]}
    characteristics: [quiescent_current]
    limits:
      quiescent_current:
        characteristic: quiescent_current
        tolerance_pct: 15
        comparator: LE                  # value <= high
```

### What happens at runtime

For each vector (e.g. `temperature=25, load=0.5`):

1. The matcher finds the product `SpecBand` whose `when:` clause matches the vector.
2. The resolver derives a nominal + accuracy from that band and applies the sidecar's `tolerance_pct` to widen it into a production limit.
3. The test body runs, `dmm.measure_dc_voltage()` returns a value, `verify` checks it against the resolved limit, and the result lands as a parquet row with `outcome=PASSED` or `FAILED`.

If no band matches the active vector and the limit has no flat top-level fields (`low:` / `high:`), the measurement records in characterization mode (`outcome=DONE`, no pass/fail). See [limits → condition-indexed bands](limits.md#condition-indexed-bands) for the precedence rules.

### Step 4 — Execute and inspect

```python
result = litmus_run(
    test="tests/test_tps54302.py",
    station="bench_1",
    serial="SN001",
    project=project,
)

print(result["status"])    # "passed" | "failed" | "error" (from subprocess returncode)
print(result["summary"])   # "1 passed in 0.42s" (parsed pytest tail line)
```

For the structured results — every measurement row, every step outcome, full traceability — query the parquet store:

```python
runs = litmus_runs(filters={"run_id": result["run_id"]}, project=project)
steps = litmus_steps(filters={"run_id": result["run_id"]}, project=project)
events = litmus_events(filters={"run_id": result["run_id"]}, project=project)
metrics = litmus_metrics(filters={"run_id": result["run_id"]}, kind="yield", project=project)
```

For visual inspection, get a UI URL:

```python
info = litmus_open(type="run", id=result["run_id"])
# Returns {"success": True, "url": "http://localhost:8000/results/<id>", "message": "..."}
```

## Limit shapes

A sidecar `limits:` entry (or the kwargs to `@pytest.mark.litmus_limits`) is a `MeasurementLimitConfig` dict (defined in `src/litmus/models/test_config.py`). At evaluation time every shape ultimately resolves to `low` / `high` / `nominal` / `comparator`.

| Shape | Example | When |
|---|---|---|
| Direct | `{low: 3.2, high: 3.4, units: V}` | Static numeric limits |
| Nominal + tolerance | `{nominal: 3.3, tolerance_pct: 5, units: V}` | Symmetric tolerance around a nominal |
| Characteristic delegation | `{characteristic: "output_voltage", tolerance_pct: 10}` | Pull nominal + accuracy from the product spec (resolves per-vector via SpecBand match). `characteristic:` is the **auto-derive trigger**; `spec_ref:` is a free-form annotation only — it does NOT look anything up. |
| Bands | `{bands: [{when: {temperature: 25}, low: 3.2, high: 3.4}, ...]}` | Condition-dependent inline bands evaluated against the vector |
| Comparator override | `{nominal: 5.0, comparator: EQ}` | Pick an ATML comparator (`EQ`/`NE`/`LT`/`LE`/`GT`/`GE`/`GELE`/`GELT`/`GTLE`/`GTLT`) |

Most common for AI-driven tests: **characteristic delegation** (when there's a product spec) and **direct** (when there isn't). See [limits how-to](limits.md) for the full resolution chain.

The plain `Limit` model (also in `test_config.py`) is what the resolver hands the runtime — it carries only the resolved `low / high / nominal / units / characteristic_id / spec_ref / comparator`. `tolerance_pct`, `bands:`, and `characteristic:` live on `MeasurementLimitConfig` (the sidecar/marker shape).

## Test code pattern

### Correct

```python
def test_output_voltage(context, psu, dmm, verify):
    """Measure output voltage at specified conditions."""
    # 1. Get test parameters from the active vector
    temperature = context.get_param("temperature", 25)
    load = context.get_param("load", 0.5)
    vin = context.get_param("vin", 12.0)

    # 2. Set up stimulus (instrument methods don't return data)
    psu.set_voltage(vin)
    psu.enable_output()

    # 3. Measure + verify — resolves the limit, raises LimitFailure on FAIL
    verify("output_voltage", dmm.measure_dc_voltage())
```

### Wrong

```python
# WRONG: hardcoded stimulus — use context.get_param so the matrix sweeps
def test_output(psu, dmm, verify):
    psu.set_voltage(12.0)
    verify("output_voltage", dmm.measure_dc_voltage())

# WRONG: bare assert instead of verify — no limit resolution, no parquet row,
# no traceability
def test_output(dmm):
    value = dmm.measure_dc_voltage()
    assert value == 3.3

# WRONG: standalone calculation — no instrument connection, no measurement
class Converter:
    def calculate_vout(self, vin):
        return vin * (1000 / (1000 + 2000))

def test_output():
    assert Converter().calculate_vout(12.0) == 4.0
```

## Checklist before generating tests

- [ ] Product spec exists with characteristics whose `bands:` cover every operating point you want to sweep.
- [ ] Each band has `when:`, `value`, and `accuracy:` populated.
- [ ] Station configured with real or mock instruments. `mock_config:` keys are method names, not signal names.
- [ ] Pulled the current test template: `litmus_project(action="read", path="template:test")`.
- [ ] Test functions are plain `def test_*(...)` taking `context`, `verify`, `logger`, and the per-role instrument fixtures as needed.
- [ ] Test reads vector parameters via `context.get_param("key", default)` — no hardcoded stimulus values.
- [ ] Test records measurements via `verify(name, value)` (raises on FAIL) or `logger.measure(name, value)` (records without judgment).
- [ ] Sidecar limits use `characteristic:` to delegate to the product spec (not `spec_ref:`, which is annotation-only).
- [ ] `tolerance_pct` applied where the spec tolerance needs widening for production margin.
- [ ] Sidecar YAML written to disk as a real `.yaml` file alongside `test_<module>.py` — not via `litmus_project(action="save", type="test")` (which forces `.py`).

## See also

- [api.md → MCP tools](../reference/api.md#mcp-tools) — full per-tool reference: parameters, return shapes, every keyword
- [cli.md → litmus setup](../reference/cli.md#litmus-setup) — `litmus setup show` and the `--print-only` flag
- [litmus-fixtures.md → context, verify, logger](../reference/litmus-fixtures.md) — every pytest fixture this page references
- [outcomes](../concepts/outcomes.md) — what each `run_outcome` / `step_outcome` / `measurement_outcome` value means
- [capabilities](../concepts/capabilities.md) — characteristics, SpecBand, the matching model
- [limits](limits.md) — the full limit-resolution chain (sidecar / marker / product spec / inline)
- [vector-expansion](vector-expansion.md) — `sweeps:` shape (cross-product vs zipped), range expanders
- [spec-driven-testing](spec-driven-testing.md) — `litmus_characteristics` + product-spec workflow
- [mock-mode](mock-mode.md) — `--mock-instruments`, `mock_config:`, the substitution pipeline
- [writing-tests](writing-tests.md) — pytest-test authoring patterns (for tests written by hand, not by an AI agent)
