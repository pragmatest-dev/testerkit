"""MCP server for AI-assisted test generation workflows.

The platform does NOT call LLMs — it exposes these tools so that AI
agents (Claude Code, etc.) can orchestrate the full datasheet-to-test
workflow.

## Tool naming convention

All tools are prefixed ``litmus_`` to namespace against other MCP
servers in a multi-server agent setup. Within the prefix:

- **Single-purpose actions** use ``litmus_<verb>``:
  ``litmus_discover`` (scan instruments), ``litmus_match`` (check
  compatibility), ``litmus_run`` (execute tests), ``litmus_open``
  (browser URL), ``litmus_schema`` (JSON Schema lookup).
- **Domain-scoped read tools** use ``litmus_<noun>`` where the noun
  is the table or store being queried. Sub-actions ride on an
  ``action=`` parameter so the tool count stays manageable:
  ``litmus_runs(action="list"|"get")``, ``litmus_steps(action=
  "list"|"tree")``, ``litmus_metrics(action="summary"|"pareto"|
  "cpk"|"trend"|"retest"|"time_loss")``. Single-action queries
  drop the ``action`` parameter: ``litmus_events``, ``litmus_
  sessions``, ``litmus_channels``, ``litmus_files``.
- **Project-scoped CRUD** is ``litmus_project`` — the unified
  entity multiplexer (init, list, get, save, read, lookup_enum,
  enum_reference) operating on a project root.

Tools shipped (12 + 1 prompt):
- ``litmus_project`` — Unified project CRUD (init, list, get,
  save, read, lookup_enum, enum_reference)
- ``litmus_discover`` — Scan for VISA instruments
- ``litmus_match`` — Check compatibility between parts /
  stations / fixtures
- ``litmus_run`` — Execute tests and return results
- ``litmus_open`` — Get URL to view/edit in browser
- ``litmus_schema`` — Get JSON Schema for YAML validation
- ``litmus_events`` — Query events from the event store
- ``litmus_sessions`` — List known sessions
- ``litmus_channels`` — Query channel data
- ``litmus_files`` — List FileStore artifacts (blobs, waveforms, streams)
- ``litmus_metrics`` — Query manufacturing-test analytics
- ``litmus_runs`` — Query the runs summary table
- ``litmus_steps`` — Query the steps table for one run
- Prompt ``datasheet-to-test`` — Full workflow guide

Tool names are part of agent prompts and therefore part of the
public contract. Renames after 0.1.0 require a deprecated-alias
window. Pick names deliberately the first time.
"""

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from litmus.mcp.tools import (
    channels_tool,
    discover_tool,
    events_tool,
    files_tool,
    litmus_tool,
    match_tool,
    metrics_tool,
    open_tool,
    run_tool,
    runs_tool,
    schema_tool,
    sessions_tool,
    steps_tool,
)
from litmus.schema_export import SCHEMA_MAP


def _load_example_snippet(relative_path: str, max_lines: int = 40) -> str:
    """Load an example file as a documentation snippet.

    Reads from the installed package's examples/ directory so examples
    always match the current code version.
    """
    examples_dir = Path(__file__).parent.parent.parent / "examples"
    path = examples_dir / relative_path
    if not path.exists():
        return f"(example file {relative_path} not found)"
    lines = path.read_text().splitlines()
    content_lines = []
    for line in lines:
        if not content_lines and line.startswith("#"):
            continue
        content_lines.append(line)
        if len(content_lines) >= max_lines:
            content_lines.append("# ... (truncated, see examples/ for full file)")
            break
    return "\n".join(content_lines)


def _build_instructions() -> str:
    """Build MCP instructions dynamically.

    - Enum values come from the schema (single source of truth for structure)
    - Examples come from examples/ files (single source of truth for usage)
    - Behavioral rules are literal strings
    """
    # Get enum values from the schema so instructions stay current
    part_schema = SCHEMA_MAP["part"].model_json_schema()
    defs = part_schema.get("$defs", {})

    # Extract MeasurementFunction enum values
    mf = defs.get("MeasurementFunction", {})
    mf_values = mf.get("enum", [])

    # Extract Direction enum values
    direction = defs.get("Direction", {})
    dir_values = direction.get("enum", [])

    # Extract Pin role enum
    pin_role = defs.get("PinRole", {})
    role_values = pin_role.get("enum", [])

    # Load examples from examples/ files (single source of truth)
    part_example = _load_example_snippet("parts/power_board.yaml", max_lines=50)
    station_example = _load_example_snippet("stations/demo_station_001.yaml", max_lines=30)

    return f"""\
Litmus: Hardware test platform. Creates tests from datasheets.

## MANDATORY: Stop and Ask at Each Step

Before proceeding to the next step, present what you found and ask for approval.
Use the most interactive/clear method available in your client:
- **Claude Desktop:** Use `ask_user_input_v0` tool for clickable widgets
- **Cursor/Cline/Others:** Present numbered choices clearly at end of message
- **Claude Code CLI:** Ask clear yes/no or multiple choice questions

Approval gates (stop at each):
0. Before init — ask user where to create the project
1. After datasheet parsing — approve extracted characteristics
2. After part spec — approve before saving
3. After instrument recommendations — choose instruments
4. After station config — approve instruments and mock values
5. After test generation — approve test code and config
6. Before execution — confirm test run parameters

**NEVER proceed without explicit user approval at each gate.**

## Workflow (All Steps Required)

```
1. Ask user where to create the project → litmus_project(action="init", path="...")
2. litmus_schema(yaml_type="part") → Get exact part schema
3. Extract specs from datasheet → Show to user → Ask approval → Save part
4. litmus_schema(yaml_type="station") → Get exact station schema
5. litmus_discover() → Show station config → Ask approval → Save station
6. litmus_schema(yaml_type="sequence") → Get sequence schema (if needed)
7. Show test plan → Ask approval → Save BOTH test .py AND config.yaml
8. Confirm ready → litmus_run() → Show results
```

**Pass `project=<path>` to ALL calls after init.**

## Schema-First Rule

**ALWAYS call `litmus_schema(yaml_type=...)` before generating ANY YAML.**
The schema defines all valid field names, types, and structure.
Do NOT guess field names — if the schema doesn't have it, don't use it.

## Key Values (from schema)

- **MeasurementFunction** enum: `{", ".join(mf_values[:10])}`, ...
  (call `litmus_schema(yaml_type="part")` for full list)
- **Direction** enum: `{", ".join(dir_values)}`
- **Pin roles**: `{", ".join(role_values) if role_values else "power, ground, signal, reference"}`

## Examples (from examples/)

### Part Spec:
```yaml
{part_example}
```

### Station Config:
```yaml
{station_example}
```

## Tools

- `litmus_project(action="init", path="~/project")` — Initialize, returns project_root
- `litmus_project(action="save", type="part|station|test", id="...",
   content={{...}}, project=...)`
- `litmus_project(action="read", path="template:test", project=...)` — Get templates
- `litmus_schema(yaml_type="part|station|catalog|sequence|fixture")` — **Call FIRST**
- `litmus_discover()` — Scan for connected instruments
- `litmus_match(requirements=[...], project=...)` — Recommend catalog instruments
- `litmus_run(test="tests/test_x.py", station="...", serial="...", project=...)`
- `litmus_project(action="lookup_enum", id="FRES")` — Resolve datasheet abbreviation
- `litmus_project(action="enum_reference")` — Full enum abbreviation table
- `litmus_open(type="part|station|run", id="...")` — Get UI URL

## Key Rules

1. **STOP at each step** — Show plan, ask approval, wait for response
2. **Pass project=** to all calls after init
3. **litmus_schema() before ANY save** — match the schema exactly
4. **Instrument `type`** — use short names (psu, dmm, scope, eload, fgen, smu)
5. **mock_config** in station for default mock values
6. **Create BOTH test files** — .py AND config.yaml
7. **_mock in config.yaml** — Per-test/per-vector mock values
8. **catalog_ref** on instruments resolves capabilities from catalog/
9. **Per-step aliases** in sequences remap fixture names to station instruments
10. **Choice format**: ALWAYS use numbered lists (1, 2, 3). NEVER use letter codes.
"""


def _build_workflow_prompt() -> str:
    """Build the datasheet-to-test workflow prompt."""
    return """\
# Datasheet to Test Workflow

You are helping create hardware tests from a part datasheet.
This is COLLABORATIVE — propose and wait for approval at each step.

## Workflow Steps

1. **Ask where to create the project** — suggest `~/litmus-<part_number>` but let the user choose.
   Then: `litmus_project(action="init", path="<user's chosen path>")`
   - Returns `project_root` — USE THIS in all subsequent calls

2. **Get Part Schema**: `litmus_schema(yaml_type="part")`
   - Read the schema carefully. Part YAML has three top-level keys:
     `part:` (header), `pins:` (physical interface), `characteristics:` (specs)
   - Characteristics use `function` (MeasurementFunction enum), `direction`,
     `units`, `pin`/`pins`, and `specs` (list of SpecBand)
   - SpecBand has: `value`, `accuracy` (pct_reading/pct_range/absolute), `when` (dict of RangeSpec)

3. **Extract & Save Part Spec**: Parse datasheet, propose characteristics,
   ask approval, save with `litmus_project(action="save", type="part", ...)`

4. **Get Station Schema**: `litmus_schema(yaml_type="station")`
   - Run `litmus_discover()` first. Use real addresses if instruments found,
     otherwise use `mock: true` with `mock_config`.

5. **Create Station Config**: Show config, ask approval, save.

6. **Create Test Files**: MUST create BOTH files
   - `tests/test_<part>.py` — plain pytest test using the ``context`` and
     ``verify`` fixtures (no decorator)
   - `tests/test_<part>.yaml` — sidecar: ``vectors``, ``limits``, and
     ``mocks`` keyed by test function name
   ```python
   litmus_project(action="save", type="test", id="tests/test_part.py", content={
       "code": "def test_foo(context, verify): ..."
   }, project=project_root)
   ```

7. **Run Tests**:
   ```python
   litmus_run(test="tests/test_part.py", station="test_bench",
              serial="TEST001", project=project_root)
   ```

## CRITICAL Rules

1. **STOP and ASK** before each step — never proceed without approval
2. **Pass project=** to ALL calls after init
3. **litmus_schema() before ANY save** — the schema is the ONLY source of truth
4. **Instrument types**: use short names (psu, dmm, eload, scope, fgen, smu)
5. **Create BOTH test files**: .py AND config.yaml
6. **_mock in config.yaml**: Per-test/per-vector mock values
7. **Choice format**: ALWAYS use numbered lists for choices. NEVER use [A], [B] letter codes.
"""


def create_mcp_server() -> FastMCP:
    """Create and configure the Litmus MCP server."""
    mcp = FastMCP(
        "Litmus",
        instructions=_build_instructions(),
    )

    # -------------------------------------------------------------------------
    # Tool 1: litmus_project (unified CRUD over project entities)
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_project")
    def litmus_project(
        action: str,
        type: str | None = None,
        id: str | None = None,
        path: str | None = None,
        content: dict[str, Any] | None = None,
        create: bool = True,
        scaffold: bool = True,
        project: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Unified Litmus operations: init, list, get, save, read.

        Actions:
        - init: Initialize project directory (returns project_root to use in subsequent calls)
          litmus_project(action="init", path="~/my-project")

        - list: List entities of a type
          litmus_project(action="list", type="part", project="/path/to/project")

        - get: Get entity details
          litmus_project(action="get", type="part", id="tps54302", project="/path/to/project")

        - save: Create/update entity
          litmus_project(action="save", type="part", id="tps54302",
                 content={...}, project="/path/to/project")

        - read: Read project file or template
          litmus_project(action="read", path="parts/x.yaml", project="/path/to/project")
          litmus_project(action="read", path="template:test", project="/path/to/project")

        - lookup_enum: Resolve datasheet abbreviations to enum values
          litmus_project(action="lookup_enum", id="FRES") → resistance_4w
          litmus_project(action="lookup_enum", id="Q") → [quality_factor, charge]

        - enum_reference: Get full abbreviation table as markdown
          litmus_project(action="enum_reference")

        Args:
            action: One of: init, list, get, save, read
            type: Entity type for list/get/save
                (part, station, fixture, sequence, catalog, instrument_asset, run, test)
            id: Entity ID for get/save
            path: Path for init/read actions
            content: Content dict for save action
            create: For init - create directory if missing (default True)
            scaffold: For init - create folder structure (default True)
            project: Project root path (required for list/get/save/read
                - use path from init response)

        Returns:
            Action-specific results.
        """
        return litmus_tool(action, type, id, path, content, create, scaffold, project)

    # -------------------------------------------------------------------------
    # Tool 2: litmus_discover
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_discover")
    def discover(protocols: list[str] | None = None) -> dict[str, Any]:
        """Scan for connected instruments across all protocols.

        Discovers instruments using the pluggable discovery system
        (VISA, NI, serial, and any registered custom protocols).

        Args:
            protocols: Protocol names to scan (e.g. ["visa", "ni", "serial"]).
                Omit to scan all registered protocols.

        Returns:
            List of discovered resources with addresses, identity, and protocol.
        """
        return discover_tool(protocols)

    # -------------------------------------------------------------------------
    # Tool 3: litmus_match
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_match")
    def match(
        part_id: str | None = None,
        station_id: str | None = None,
        fixture_id: str | None = None,
        requirements: list[dict[str, Any]] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Check compatibility between parts, stations, and fixtures.

        Usage patterns:
        - match(requirements=[...], project="...") → Recommend catalog instruments
        - match(part_id="...") → Find compatible stations, derive requirements
        - match(part_id="...", station_id="...") → Detailed compatibility check
        - match(fixture_id="...", project="...") → Find stations with required instruments

        Requirements format (for catalog recommendations):
        ```python
        litmus_match(requirements=[
            {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"},
            {"function": "dc_voltage", "direction": "output", "range_max": 12, "units": "V"},
            {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V",
             "accuracy": {"pct_reading": 0.01, "pct_range": 0.005}},
            {"function": "ac_voltage", "direction": "input", "range_max": 10, "units": "V",
             "conditions": {"frequency": {"min": 1000, "max": 100000, "units": "Hz"}}},
            {"function": "dc_voltage", "direction": "input",
             "resolution": {"digits": 6.5}},
        ], project=".")
        ```

        Args:
            part_id: Part ID to check compatibility for
            station_id: Station ID for detailed check (requires part_id)
            fixture_id: Fixture ID to find compatible stations
            requirements: Ad-hoc capability requirements for catalog instrument
                recommendations. Each dict: function (required), direction (required),
                range_max, range_min, units (optional), accuracy (optional dict with
                pct_reading/pct_range/absolute), resolution (optional dict with
                digits/bits/value/units), conditions (optional dict of condition
                dicts with min/max/units).
            project: Project root path (required for fixture/requirements matching)

        Returns:
            Compatibility results with requirements and matches.
        """
        return match_tool(part_id, station_id, fixture_id, requirements, project)

    # -------------------------------------------------------------------------
    # Tool 4: litmus_run
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_run")
    def run(test: str, station: str, serial: str, project: str) -> dict[str, Any]:
        """Execute tests and return results.

        Runs pytest with the specified test path and waits for completion.
        Returns full results including pass/fail status and measurements.

        Args:
            test: Test file or directory (e.g., "tests/test_x.py")
            station: Station ID to run on
            serial: UUT serial number
            project: Project root path (from litmus action='init' response)

        Returns:
            Run results with outcome, measurements, and any errors.
        """
        return run_tool(test, station, serial, project)

    # -------------------------------------------------------------------------
    # Tool 5: litmus_open
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_open")
    def open_ui(type: str, id: str, base_url: str = "http://localhost:8000") -> dict[str, Any]:
        """Get URL to view/edit an entity in the browser UI.

        Use this when detailed viewing or visual editing is needed.

        Args:
            type: Entity type (part, station, run, fixture, sequence)
            id: Entity ID
            base_url: UI server URL (default: http://localhost:8000)

        Returns:
            URL to open in browser.
        """
        return open_tool(type, id, base_url)

    # -------------------------------------------------------------------------
    # Tool 6: litmus_schema
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_schema")
    def schema(yaml_type: str | None = None) -> dict[str, Any]:
        """Get JSON Schema for a Litmus YAML file type.

        CALL THIS BEFORE generating any YAML. The schema is the single
        source of truth for field names, types, enums, and structure.

        Args:
            yaml_type: A file type (e.g. catalog, part, station, sequence,
                fixture, instrument_asset, project). Omit to list available types.

        Returns:
            JSON Schema for the requested YAML type.
        """
        return schema_tool(yaml_type)

    # -------------------------------------------------------------------------
    # Tool 7: litmus_events
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_events")
    def query_events(
        session_id: str | None = None,
        event_type: str | None = None,
        role: str | None = None,
        since: str | None = None,
        limit: int = 100,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Query events from the event store.

        Args:
            session_id: Filter by session UUID.
            event_type: Filter by event type (e.g. "instrument.read", "session.started").
            role: Filter by instrument role.
            since: ISO timestamp — only events after this time.
            limit: Max events to return (default 100).
            project: Project root path.
        """
        return events_tool(session_id, event_type, role, since, limit, project)

    # -------------------------------------------------------------------------
    # Tool 8: litmus_sessions
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_sessions")
    def list_sessions(project: str | None = None) -> dict[str, Any]:
        """List known sessions with metadata.

        Returns SessionStarted events for all sessions.

        Args:
            project: Project root path.
        """
        return sessions_tool(project)

    # -------------------------------------------------------------------------
    # Tool 9: litmus_channels
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_channels")
    def query_channels(
        channel_id: str,
        session_id: str | None = None,
        last_n: int | None = None,
        max_points: int | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Query channel data from the streaming channel store.

        Args:
            channel_id: Channel to query (e.g. "scope.ch1_waveform").
            session_id: Filter to a specific session.
            last_n: Return only the last N rows.
            max_points: Downsample to at most this many rows (LTTB).
            project: Project root path.
        """
        return channels_tool(channel_id, session_id, last_n, max_points, project)

    # -------------------------------------------------------------------------
    # Tool: litmus_files
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_files")
    def query_files(
        uri: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 50,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List FileStore artifacts (blobs, waveforms, streaming captures).

        Returns catalog rows newest-first — each carrying its ``file://``
        URI, name, format, session_id, run_id, and created_at. Fetch an
        artifact's bytes separately via its URI (HTTP ``GET /files?uri=``).

        Args:
            uri: Return the single artifact with this ``file://`` URI.
            session_id: Filter to artifacts written by this session.
            run_id: Filter to artifacts produced by this run.
            limit: Maximum rows to return (newest first).
            project: Project root path.
        """
        return files_tool(uri, session_id, run_id, limit, project)

    # -------------------------------------------------------------------------
    # Tool 10: litmus_metrics
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_metrics")
    def query_metrics(
        action: str,
        part: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
        top_n: int = 10,
        min_samples: int = 10,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Query manufacturing-test analytics (DuckDB SQL aggregated from parquet rows).

        Fast analytics without loading all data into Python. Supports:
        - summary: FPY, final yield, run counts, duration stats
        - pareto: Top failure modes by count
        - cpk: Process capability (Cpk/Cp) per measurement
        - trend: Yield trend over time
        - retest: Retest rates per serial
        - time_loss: Time lost to failures and errors

        Args:
            action: One of: summary, pareto, cpk, trend, retest, time_loss.
            part: Filter by part/part number.
            station: Filter by station name.
            phase: Test phase (default: exclude development, 'all' = no filter).
            since: Start date (ISO format, inclusive).
            until: End date (ISO format, inclusive).
            period: Time bucket — day, week, or month.
            top_n: Number of top failures for pareto.
            min_samples: Minimum sample count for cpk.
            project: Project root path.
        """
        return metrics_tool(
            action,
            part=part,
            station=station,
            phase=phase,
            since=since,
            until=until,
            period=period,
            top_n=top_n,
            min_samples=min_samples,
            project=project,
        )

    # -------------------------------------------------------------------------
    # Tool 11: litmus_runs
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_runs")
    def query_runs(
        action: str = "list",
        run_id: str | None = None,
        limit: int = 50,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Query the runs table — denormalized run-level summaries.

        Args:
            action: ``list`` (most recent runs) or ``get`` (one run by id).
            run_id: Required when ``action='get'``; full UUID or 8-char prefix.
            limit: Max rows when ``action='list'`` (default 50).
            project: Project root path.
        """
        if action not in ("list", "get"):
            return {"error": f"Unknown action '{action}'. Valid: ['list', 'get']"}
        return runs_tool(action=action, run_id=run_id, limit=limit, project=project)  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Tool 12: litmus_steps
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_steps")
    def query_steps(
        run_id: str,
        action: str = "list",
        project: str | None = None,
    ) -> dict[str, Any]:
        """Query the steps table for one run.

        Args:
            run_id: Full UUID or 8-char prefix of the run.
            action: ``list`` for flat ordered rows; ``tree`` for the
                ``step_path``-derived hierarchy.
            project: Project root path.
        """
        if action not in ("list", "tree"):
            return {"error": f"Unknown action '{action}'. Valid: ['list', 'tree']"}
        return steps_tool(run_id=run_id, action=action, project=project)  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Prompt: datasheet-to-test workflow
    # -------------------------------------------------------------------------

    @mcp.prompt(name="datasheet-to-test")
    def datasheet_to_test_prompt() -> str:
        """Get the full datasheet-to-test workflow guide.

        Use this prompt when starting a new test creation workflow from a datasheet.
        It provides step-by-step instructions for the complete workflow.
        """
        return _build_workflow_prompt()

    return mcp


def run_mcp_server():
    """Run the MCP server (for CLI entry point)."""
    mcp = create_mcp_server()
    mcp.run()
