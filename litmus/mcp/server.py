"""MCP server for AI-assisted test generation workflows.

This server exposes 6 tools:
- litmus: Unified CRUD operations (init, list, get, save, read)
- litmus_discover: Scan for VISA instruments
- litmus_match: Check compatibility between products/stations/fixtures
- litmus_run: Execute tests and return results
- litmus_open: Get URL to view/edit in browser
- litmus_schema: Get JSON Schema for YAML validation/generation

The platform does NOT call LLMs - it exposes these tools so that AI agents
(Claude Code, etc.) can orchestrate the full datasheet-to-test workflow.
"""

from typing import Any

from fastmcp import FastMCP

from litmus.mcp.tools import (
    discover_tool,
    litmus_tool,
    match_tool,
    open_tool,
    run_tool,
    schema_tool,
)


def _load_demo_snippet(relative_path: str, max_lines: int = 40) -> str:
    """Load a demo file as an example snippet.

    Reads from the installed package's demo/ directory so examples
    always match the current code version.
    """
    from pathlib import Path

    # demo/ is at repo root, same level as litmus/
    demo_dir = Path(__file__).parent.parent.parent / "demo"
    path = demo_dir / relative_path
    if not path.exists():
        return f"(example file {relative_path} not found)"
    lines = path.read_text().splitlines()
    # Skip comment header, take first max_lines of content
    content_lines = []
    for line in lines:
        if not content_lines and line.startswith("#"):
            continue  # skip leading comments
        content_lines.append(line)
        if len(content_lines) >= max_lines:
            content_lines.append("# ... (truncated, see demo/ for full file)")
            break
    return "\n".join(content_lines)


def _build_instructions() -> str:
    """Build MCP instructions dynamically.

    - Enum values come from the schema (single source of truth for structure)
    - Examples come from demo/ files (single source of truth for usage)
    - Behavioral rules are literal strings
    """
    # Get enum values from the schema so instructions stay current
    from litmus.schemas import SCHEMA_MAP

    product_schema = SCHEMA_MAP["product"].model_json_schema()
    defs = product_schema.get("$defs", {})

    # Extract MeasurementFunction enum values
    mf = defs.get("MeasurementFunction", {})
    mf_values = mf.get("enum", [])

    # Extract Direction enum values
    direction = defs.get("Direction", {})
    dir_values = direction.get("enum", [])

    # Extract Pin role enum
    pin_role = defs.get("PinRole", {})
    role_values = pin_role.get("enum", [])

    # Load examples from demo/ files (single source of truth)
    product_example = _load_demo_snippet("products/power_board/spec.yaml", max_lines=50)
    station_example = _load_demo_snippet("stations/demo_station_001.yaml", max_lines=30)

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
2. After product spec — approve before saving
3. After instrument recommendations — choose instruments
4. After station config — approve instruments and mock values
5. After test generation — approve test code and config
6. Before execution — confirm test run parameters

**NEVER proceed without explicit user approval at each gate.**

## Workflow (All Steps Required)

```
1. Ask user where to create the project → litmus(action="init", path="...")
2. litmus_schema(yaml_type="product") → Get exact product schema
3. Extract specs from datasheet → Show to user → Ask approval → Save product
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

- **MeasurementFunction** enum: `{', '.join(mf_values[:10])}`, ...
  (call `litmus_schema(yaml_type="product")` for full list)
- **Direction** enum: `{', '.join(dir_values)}`
- **Pin roles**: `{', '.join(role_values) if role_values else 'power, ground, signal, reference'}`

## Examples (from demo/)

### Product Spec:
```yaml
{product_example}
```

### Station Config:
```yaml
{station_example}
```

## Tools

- `litmus(action="init", path="~/project")` — Initialize, returns project_root
- `litmus(action="save", type="product|station|test", id="...", content={{...}}, project=...)`
- `litmus(action="read", path="template:test", project=...)` — Get templates
- `litmus_schema(yaml_type="product|station|catalog|sequence|fixture")` — **Call FIRST**
- `litmus_discover()` — Scan for connected instruments
- `litmus_match(requirements=[...], project=...)` — Recommend catalog instruments
- `litmus_run(test="tests/test_x.py", station="...", serial="...", project=...)`
- `litmus(action="lookup_enum", id="FRES")` — Resolve datasheet abbreviation
- `litmus(action="enum_reference")` — Full enum abbreviation table
- `litmus_open(type="product|station|run", id="...")` — Get UI URL

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
    """Build the datasheet-to-test workflow prompt.

    References litmus_schema() instead of hardcoding YAML examples.
    """
    return """\
# Datasheet to Test Workflow

You are helping create hardware tests from a product datasheet.
This is COLLABORATIVE — propose and wait for approval at each step.

## Workflow Steps

1. **Ask where to create the project** — suggest `~/litmus-<part_number>` but let the user choose.
   Then: `litmus(action="init", path="<user's chosen path>")`
   - Returns `project_root` — USE THIS in all subsequent calls

2. **Get Product Schema**: `litmus_schema(yaml_type="product")`
   - Read the schema carefully. Product YAML has three top-level keys:
     `product:` (header), `pins:` (physical interface), `characteristics:` (specs)
   - Characteristics use `function` (MeasurementFunction enum), `direction`,
     `units`, `pin`/`pins`, and `specs` (list of SpecBand)
   - SpecBand has: `value`, `accuracy` (pct_reading/pct_range/absolute), `when` (dict of RangeSpec)

3. **Extract & Save Product Spec**: Parse datasheet, propose characteristics,
   ask approval, save with `litmus(action="save", type="product", ...)`

4. **Get Station Schema**: `litmus_schema(yaml_type="station")`
   - Run `litmus_discover()` first. Use real addresses if instruments found,
     otherwise use `mock: true` with `mock_config`.

5. **Create Station Config**: Show config, ask approval, save.

6. **Create Test Files**: MUST create BOTH files
   - `tests/test_<part>.py` — Python test using `@litmus_test` decorator
   - `tests/config.yaml` — vectors, limits, and _mock values
   ```python
   litmus(action="save", type="test", id="tests/test_part.py", content={
       "code": "from litmus.execution import litmus_test\\n..."
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
    # Tool 1: litmus (unified CRUD)
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus")
    def litmus(
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
          litmus(action="init", path="~/my-project")

        - list: List entities of a type
          litmus(action="list", type="product", project="/path/to/project")

        - get: Get entity details
          litmus(action="get", type="product", id="tps54302", project="/path/to/project")

        - save: Create/update entity
          litmus(action="save", type="product", id="tps54302",
                 content={...}, project="/path/to/project")

        - read: Read project file or template
          litmus(action="read", path="products/x/spec.yaml", project="/path/to/project")
          litmus(action="read", path="template:test", project="/path/to/project")

        - lookup_enum: Resolve datasheet abbreviations to enum values
          litmus(action="lookup_enum", id="FRES") → resistance_4w
          litmus(action="lookup_enum", id="Q") → [quality_factor, charge]

        - enum_reference: Get full abbreviation table as markdown
          litmus(action="enum_reference")

        Args:
            action: One of: init, list, get, save, read
            type: Entity type for list/get/save
                (product, station, fixture, sequence, catalog, instrument_asset, run, test)
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
        product_id: str | None = None,
        station_id: str | None = None,
        fixture_id: str | None = None,
        requirements: list[dict[str, Any]] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Check compatibility between products, stations, and fixtures.

        Usage patterns:
        - match(requirements=[...], project="...") → Recommend catalog instruments
        - match(product_id="...") → Find compatible stations, derive requirements
        - match(product_id="...", station_id="...") → Detailed compatibility check
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
            product_id: Product ID to check compatibility for
            station_id: Station ID for detailed check (requires product_id)
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
        return match_tool(product_id, station_id, fixture_id, requirements, project)

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
            serial: DUT serial number
            project: Project root path (from litmus action='init' response)

        Returns:
            Run results with outcome, measurements, and any errors.
        """
        return run_tool(test, station, serial, project)

    # -------------------------------------------------------------------------
    # Tool 5: litmus_open
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_open")
    def open_ui(
        type: str, id: str, base_url: str = "http://localhost:8000"
    ) -> dict[str, Any]:
        """Get URL to view/edit an entity in the browser UI.

        Use this when detailed viewing or visual editing is needed.

        Args:
            type: Entity type (product, station, run, fixture, sequence)
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
            yaml_type: A file type (e.g. catalog, product, station, sequence,
                fixture, instrument_asset, project). Omit to list available types.

        Returns:
            JSON Schema for the requested YAML type.
        """
        return schema_tool(yaml_type)

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
