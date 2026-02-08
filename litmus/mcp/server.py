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


def create_mcp_server() -> FastMCP:
    """Create and configure the Litmus MCP server."""
    mcp = FastMCP(
        "Litmus",
        instructions="""Litmus: Hardware test platform. Creates tests from datasheets.

## MANDATORY: Stop and Ask at Each Step

Before EVERY action, show what you'll do and ask for approval.
Never proceed without user confirmation.

### Presenting Choices
When asking the user to choose, present options as a **numbered list**
at the end of your message. Keep any explanation ABOVE the list.
Never bury choices mid-paragraph or use inline `[A] [B] [C]` codes.

## Workflow (All Steps Required)

```
1. litmus(action="init", path="...") → Get project path
2. Show extracted specs → Ask approval → Save product
3. Show station config → Ask approval → Save station
4. Show test plan → Ask approval → Save BOTH test files
5. Confirm ready → Run tests → Show results
```

**Pass `project=<path>` to ALL calls after init.**

## Critical Formats

### Product Spec (pins with roles):
```yaml
product:
  id: power_board
  name: "Power Board Rev A"
pins:
  J1_VIN:
    name: "J1.1"
    net: "VIN_5V"
    role: power          # power/ground/signal/reference
  J1_GND:
    name: "J1.2"
    net: "GND"
    role: ground
  TP_VOUT:
    name: "TP2"
    net: "VOUT_3V3"
    # role: signal (default, can omit)
characteristics:
  output_voltage:
    function: dc_voltage    # MeasurementFunction enum
    direction: output       # DUT provides this signal
    units: V
    pin: TP_VOUT
    conditions:
      - nominal: 3.3
        tolerance_pct: 2
```

### Station Config:
**IMPORTANT:** Run `litmus_discover()` FIRST. Use real addresses if instruments
are found, otherwise use `mock: true` with `mock_config`.
```yaml
station:
  id: my_station
  name: "Test Bench"
instruments:
  psu:
    type: psu
    driver: drivers.PSU
    resource: ""           # Fill from litmus_discover() results
    catalog_ref: keysight_e36312a
    channels: ["1", "2"]
    mock: true             # Set false when using real instruments
    mock_config:
      voltage: 5.0
      current: 0.5
  dmm:
    type: dmm
    driver: drivers.DMM
    resource: ""           # Fill from litmus_discover() results
    catalog_ref: keysight_34461a
    mock: true
    mock_config:
      voltage: 3.3
```
Catalog entries define structured channel topology (terminals, connector, ground mode)
and capabilities with optional `readback: true` for built-in meters (PSU/eload voltage readback).

### Test Files (MUST create both):

**tests/test_xxx.py:**
```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    psu.set_voltage(context.get_in("vin", 5.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

**tests/config.yaml:**
```yaml
test_output_voltage:
  vectors:
    - vin: 5.0
  _mock:
    dmm.measure_voltage: 3.3
  limits:
    test_output_voltage:
      low: 3.1
      high: 3.5
      nominal: 3.3
      units: V
```

## Tools

- `litmus(action="init", path="~/project")` - Initialize, returns project_root
- `litmus(action="save", type="product|station|test", id="...", content={...}, project=project)`
- `litmus(action="read", path="template:test", project=project)` - Get templates
- `litmus_run(test="tests/test_x.py", station="...", serial="...", project=project)`
- `litmus_open(type="product|station|run", id="...")` - Get UI URL

## Key Rules

1. **STOP at each step** - Show plan, ask approval, wait for response
2. **Pass project=** to all calls after init
3. **Instrument `type`** — use short names (e.g. psu, dmm, scope, eload, fgen, smu)
4. **mock_config** in station for default mock values
5. **Create BOTH test files** - .py AND config.yaml
6. **_mock in config.yaml** - Per-test/per-vector mock values
7. **Pin roles:** power, ground, signal (default), reference
8. **catalog_ref** on instruments resolves capabilities from catalog/
9. **Per-step aliases** in sequences remap fixture names to station instruments:
   ```yaml
   steps:
     - id: precision_cal
       test: tests/test_cal.py::test_voltage
       aliases:
         dmm: precision_dmm
   ```
   Without aliases, fixture name = station role name (default, zero config).
""",
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
    ) -> dict[str, Any]:
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

        Args:
            action: One of: init, list, get, save, read
            type: Entity type for list/get/save
                (product, station, fixture, sequence, instrument, run, test)
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
        ], project=".")
        ```

        Args:
            product_id: Product ID to check compatibility for
            station_id: Station ID for detailed check (requires product_id)
            fixture_id: Fixture ID to find compatible stations
            requirements: Ad-hoc capability requirements for catalog instrument
                recommendations. Each dict: function (required), direction (required),
                range_max, range_min, units (optional).
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

        Use this before generating YAML to get the exact structure, required
        fields, and enum values for validation.

        Args:
            yaml_type: One of: catalog, product, station, sequence, fixture.
                Omit to list available types.

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
        return '''# Datasheet to Test Workflow

You are helping create hardware tests from a product datasheet.
This is COLLABORATIVE - propose and wait for approval at each step.

## Workflow Steps

1. **Init Project**: `litmus(action="init", path="~/project-name")`
   - Returns `project_root` - USE THIS in all subsequent calls

2. **Extract & Save Product Spec**: Read datasheet, extract specs, ask approval
   ```python
   litmus(action="save", type="product", id="part_number", content={
       "product": {"id": "part_number", "name": "Full Name", ...},
       "specs": {"input_voltage": {"min": 4.5, "max": 28, "unit": "V"}, ...}
   }, project=project_root)
   ```

3. **Create Station Config**: Run `litmus_discover()` first. If instruments are
   found, use their real addresses. If not, use `mock: true` with `mock_config`.
   Show config, ask approval, save.

### Optional: Sequence with Per-Step Aliases

If the station has multiple instruments of the same type, create a sequence with per-step aliases:

```yaml
sequence:
  id: full_test
  description: "Full test with instrument selection"
  steps:
    - id: precision_cal
      test: tests/test_partnum.py::test_output_voltage
      aliases:
        dmm: precision_dmm
    - id: quick_check
      test: tests/test_partnum.py::test_output_voltage
      aliases:
        dmm: fast_dmm
```

Only needed when different steps need different instruments for the same fixture name.

4. **Create Test Files**: MUST create BOTH files
   ```python
   # File 1: test code
   litmus(action="save", type="test", id="tests/test_partnum.py", content={
       "code": """from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, psu, dmm):
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()
"""
   }, project=project_root)

   # File 2: config with limits and mocks
   litmus(action="save", type="test", id="tests/config.yaml", content={
       "code": """test_output_voltage:
  vectors:
    - vin: 12.0
  _mock:
    dmm.measure_voltage: 5.0
  limits:
    test_output_voltage:
      low: 4.75
      high: 5.25
      nominal: 5.0
      units: V
"""
   }, project=project_root)
   ```

5. **Run Tests**:
   ```python
   litmus_run(test="tests/test_partnum.py", station="test_bench",
              serial="TEST001", project=project_root)
   ```

## CRITICAL Rules

1. **STOP and ASK** before each step - never proceed without approval
2. **Pass project=** to ALL calls after init
3. **Call `litmus_schema()`** before ANY save — match the schema exactly, no invented fields
4. **Instrument types**: use short names (e.g. psu, dmm, eload, scope, fgen, smu)
4. **mock_config** in station for default mock values
5. **Create BOTH test files**: .py AND config.yaml
6. **_mock in config.yaml**: Per-test/per-vector mock values
7. **Choice format**: ALWAYS use numbered lists for choices. Example:
   ```
   What would you like to do?
   1. Save as-is
   2. Adjust the output voltage target
   3. Add thermal testing
   ```
   NEVER use `[A]`, `[B]`, `[?]` letter codes. NEVER.
'''

    return mcp


def run_mcp_server():
    """Run the MCP server (for CLI entry point)."""
    mcp = create_mcp_server()
    mcp.run()
