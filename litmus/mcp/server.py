"""MCP server for AI-assisted test generation workflows.

This server exposes 9 tools for:
- discover: Scan for VISA instruments
- list: List entities (stations, products, fixtures, sequences, instruments, runs)
- get: Get entity details
- save: Create/update entities
- match: Check compatibility between products, stations, fixtures
- run: Execute test sequences
- status: Get run status
- open_ui: Get URL to view/edit in browser
- read: Read project files (datasheets, specs, tests)

The platform does NOT call LLMs - it exposes these tools so that AI agents
(Claude Code, etc.) can orchestrate the full datasheet-to-test workflow.
"""

from typing import Any

from fastmcp import FastMCP

from litmus.mcp.tools import (
    discover_tool,
    get_tool,
    list_tool,
    match_tool,
    open_ui_tool,
    read_tool,
    run_tool,
    save_tool,
    status_tool,
)


def create_mcp_server() -> FastMCP:
    """Create and configure the Litmus MCP server."""
    mcp = FastMCP(
        "Litmus",
        instructions="""Litmus is a hardware test platform. Use these 9 tools:

1. litmus_discover - Scan for connected VISA instruments
2. litmus_list - List entities (station/product/fixture/sequence/instrument/run)
3. litmus_get - Get full details of an entity by type and ID
4. litmus_save - Create or update an entity
5. litmus_match - Check compatibility between products, stations, fixtures
6. litmus_run - Execute a test sequence
7. litmus_status - Get test run status and results
8. litmus_open_ui - Get URL to view/edit entity in browser
9. litmus_read - Read project files (datasheets, specs, tests)

Project structure:
- demo/datasheets/ - Product datasheets (.md files)
- demo/specs/ - Product specifications (.yaml)
- demo/stations/ - Station configurations (.yaml)
- demo/tests/ - Test files (.py)
- template:test - Test template using @litmus_test decorator

IMPORTANT: Use @litmus_test decorator for tests, NOT TestHarness directly.
See demo/tests/test_power_board.py or litmus_read("template:test") for examples.

Typical workflow:
1. litmus_read("demo/datasheets/") → list available datasheets
2. litmus_read("demo/datasheets/tps54302.md") → read datasheet content
3. litmus_save(product, ...) → create product spec from datasheet
4. litmus_list(station) → see available test stations
5. litmus_match(product_id) → find compatible stations
6. litmus_save(test, ...) → generate pytest test code
7. litmus_run(...) → execute tests
8. litmus_status(...) → check results
""",
    )

    # -------------------------------------------------------------------------
    # Tool 1: discover
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_discover")
    def discover() -> dict[str, Any]:
        """Scan for connected VISA instruments.

        Discovers available VISA resources on this computer. Returns a list of
        instruments with their addresses, connection types, and identification.

        Use this as the first step when setting up a new test station.

        Returns:
            List of discovered resources with addresses and suggested types.
        """
        return discover_tool()

    # -------------------------------------------------------------------------
    # Tool 2: list
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_list")
    def list_entities(entity_type: str) -> list[dict[str, Any]] | dict[str, Any]:
        """List entities of a given type.

        Args:
            entity_type: One of:
                - station: Test station configurations
                - product: Product specifications
                - fixture: Fixture/pinmap configurations
                - sequence: Test sequences
                - instrument: Instrument library definitions
                - run: Test run results

        Returns:
            List of entities with id, name, and basic info.
        """
        return list_tool(entity_type)

    # -------------------------------------------------------------------------
    # Tool 3: get
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_get")
    def get_entity(entity_type: str, entity_id: str) -> dict[str, Any]:
        """Get full details of an entity.

        Args:
            entity_type: One of: station, product, fixture, sequence, instrument, run
            entity_id: The entity ID (e.g., "bench_1", "tps54302")

        Returns:
            Full entity details including all nested data.
        """
        return get_tool(entity_type, entity_id)

    # -------------------------------------------------------------------------
    # Tool 4: save
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_save")
    def save_entity(entity_type: str, entity_id: str, content: dict[str, Any]) -> dict[str, Any]:
        """Create or update an entity.

        Validates the content before saving. Returns the path to the saved file
        or validation errors.

        Args:
            entity_type: One of: station, product, fixture, sequence, instrument, test
            entity_id: The entity ID (used as filename)
            content: The entity content to save (structure varies by type)

        Returns:
            Result with path to saved file or validation errors.

        Content structure by type:
        - station: {id, name, instruments: {name: {type, resource}}}
        - product: {product: {id, name}, characteristics: {...}, test_requirements: {...}}
        - fixture: {fixture: {id}, points: {name: {instrument, instrument_channel}}}
        - sequence: {sequence: {id, description, steps: [...]}}
        - instrument: {instrument: {type, name}, capabilities: [...]}
        - test: {code: "...python source..."}
        """
        return save_tool(entity_type, entity_id, content)

    # -------------------------------------------------------------------------
    # Tool 5: match
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_match")
    def match(
        product_id: str | None = None,
        station_id: str | None = None,
        fixture_id: str | None = None,
    ) -> dict[str, Any]:
        """Check compatibility between products, stations, and fixtures.

        Usage patterns:
        - match(product_id="...") → Find compatible stations, derive requirements
        - match(product_id="...", station_id="...") → Detailed compatibility check
        - match(fixture_id="...") → Find stations with required instruments

        Args:
            product_id: Product ID to check compatibility for
            station_id: Station ID for detailed check (requires product_id)
            fixture_id: Fixture ID to find compatible stations

        Returns:
            Compatibility results with requirements and matches.
        """
        return match_tool(product_id, station_id, fixture_id)

    # -------------------------------------------------------------------------
    # Tool 6: run
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_run")
    def run_tests(sequence_id: str, station_id: str, dut_serial: str) -> dict[str, Any]:
        """Execute a test sequence.

        Starts a test run and returns a run_id for tracking progress.

        Args:
            sequence_id: The sequence to run
            station_id: Which station to run on
            dut_serial: Serial number of device under test

        Returns:
            Run info with run_id for tracking progress via status().
        """
        return run_tool(sequence_id, station_id, dut_serial)

    # -------------------------------------------------------------------------
    # Tool 7: status
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_status")
    def status(run_id: str) -> dict[str, Any]:
        """Get status of a test run.

        Args:
            run_id: The run ID returned from run()

        Returns:
            Run status including outcome, step counts, and timing.
        """
        return status_tool(run_id)

    # -------------------------------------------------------------------------
    # Tool 8: open_ui
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_open_ui")
    def open_ui(
        entity_type: str, id: str, base_url: str = "http://localhost:8000"
    ) -> dict[str, Any]:
        """Get URL to view/edit an entity in the browser UI.

        Use this when detailed viewing or visual editing is needed.

        Args:
            entity_type: One of: product, station, run, fixture, sequence
            id: Entity ID
            base_url: UI server URL (default: http://localhost:8000)

        Returns:
            URL to open in browser.
        """
        return open_ui_tool(entity_type, id, base_url)

    # -------------------------------------------------------------------------
    # Tool 9: read
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_read")
    def read_file(path: str) -> dict[str, Any]:
        """Read a file or list directory contents from the project.

        Use this to access datasheets, specs, tests, and other project files.
        Paths are relative to the project root.

        Common paths:
        - demo/datasheets/ - Product datasheets
        - demo/specs/ - Product specifications
        - demo/stations/ - Station configurations
        - demo/tests/ - Test files

        Args:
            path: Relative path (e.g., "demo/datasheets/tps54302.md")

        Returns:
            File contents or directory listing.
        """
        return read_tool(path)

    return mcp


def run_mcp_server():
    """Run the MCP server (for CLI entry point)."""
    mcp = create_mcp_server()
    mcp.run()
