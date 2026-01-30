"""MCP server for AI-assisted test generation workflows.

This server exposes tools for:
- Reading product specs, station configs, and instrument libraries
- Capability matching (deterministic)
- Writing new specs, instruments, sequences, and tests
- Running tests and checking status

The platform does NOT call LLMs - it exposes these tools so that AI agents
(Claude Code, etc.) can orchestrate the full datasheet-to-test workflow.
"""

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from litmus.mcp.tools import (
    check_station_compatibility_tool,
    complete_workflow_step_tool,
    create_product_folder_tool,
    create_station_tool,
    derive_required_capabilities_tool,
    discover_visa_resources_tool,
    dry_run_sequence_tool,
    find_compatible_stations_tool,
    get_compatible_stations_for_fixture_tool,
    get_editor_url_tool,
    get_fixture_config_tool,
    get_fixtures_for_product_tool,
    get_instrument_library_tool,
    get_product_folder_tool,
    get_product_spec_tool,
    get_run_status_tool,
    get_station_config_tool,
    get_test_templates_tool,
    list_available_instrument_types_tool,
    list_fixtures_tool,
    list_instrument_types_tool,
    list_product_folders_tool,
    list_products_tool,
    list_sequences_tool,
    list_stations_tool,
    run_sequence_tool,
    save_fixture_config_tool,
    save_instrument_library_tool,
    save_product_spec_to_folder_tool,
    save_product_spec_tool,
    save_test_file_tool,
    save_test_sequence_tool,
    validate_fixture_config_tool,
    validate_product_spec_tool,
    validate_test_sequence_tool,
)


def create_mcp_server() -> FastMCP:
    """Create and configure the Litmus MCP server."""
    mcp = FastMCP(
        "Litmus",
        instructions="""Litmus is a hardware test platform. Use these tools to:

1. SETUP: Discover instruments, create station configs
2. EXPLORE: List and read products, stations, instruments, sequences
3. MATCH: Find which stations can test which products (deterministic capability matching)
4. CREATE: Save new product specs, test sequences, test code
5. EXECUTE: Run tests and check results

Typical workflow (starting from nothing):
1. discover_visa_resources → see what instruments are connected
2. list_available_instrument_types → see what drivers are available
3. create_station → create station config from discovered instruments
4. create_product_folder → start a new product workflow
5. Read datasheet (user provides file) → create product spec from characteristics
6. save_product_spec_to_folder → save the spec
7. derive_required_capabilities → find_compatible_stations
8. save_test_file → generate pytest test code
9. save_test_sequence → define test order
10. run_sequence → execute tests
""",
    )

    # -----------------------------------------------------------------------------
    # Station Discovery Tools
    # -----------------------------------------------------------------------------

    @mcp.tool
    def discover_visa_resources() -> dict[str, Any]:
        """Discover connected VISA instruments on this computer.

        Scans for available VISA resources using PyVISA. Returns a list of
        discovered instruments with their addresses, types, and identification.

        Use this as the first step when setting up a new test station.

        Returns:
            List of discovered resources with addresses and suggested types.
        """
        return discover_visa_resources_tool()

    @mcp.tool
    def create_station(
        station_id: str,
        name: str,
        instruments: list[dict[str, str]],
        location: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new station configuration.

        Use this after discover_visa_resources to create a station config
        from discovered instruments.

        Args:
            station_id: Unique identifier (e.g., "bench_1")
            name: Human-readable name (e.g., "Main Test Bench")
            instruments: List of instruments, each with:
                - name: Instrument name (e.g., "dmm_main", "psu_dut")
                - type: Instrument type (e.g., "dmm", "psu")
                - address: VISA address
            location: Optional location (e.g., "Lab A Room 101")
            description: Optional description

        Returns:
            Path to created station config file.
        """
        return create_station_tool(station_id, name, instruments, location, description)

    @mcp.tool
    def list_available_instrument_types() -> list[dict[str, Any]]:
        """List instrument types with drivers available.

        Use this to see what instrument types can be used when creating
        a station. Each type has specific capabilities.

        Returns:
            List of instrument types with names, descriptions, and capabilities.
        """
        return list_available_instrument_types_tool()

    # -----------------------------------------------------------------------------
    # Read/Context Tools
    # -----------------------------------------------------------------------------

    @mcp.tool
    def list_products() -> list[dict[str, Any]]:
        """List all available product specifications.

        Returns a list of products with their IDs, names, and counts of
        characteristics and test requirements.
        """
        return list_products_tool()

    @mcp.tool
    def get_product_spec(product_id: str) -> dict[str, Any]:
        """Get full product specification by ID.

        Args:
            product_id: The product ID (e.g., "power_board_v1")

        Returns:
            Full product spec including characteristics and test requirements.
        """
        return get_product_spec_tool(product_id)

    @mcp.tool
    def list_stations() -> list[dict[str, Any]]:
        """List all available test stations.

        Returns a list of stations with their IDs, names, locations, and descriptions.
        """
        return list_stations_tool()

    @mcp.tool
    def get_station_config(station_id: str) -> dict[str, Any]:
        """Get full station configuration by ID.

        Args:
            station_id: The station ID (e.g., "demo_station_001")

        Returns:
            Full station config including instruments and their addresses.
        """
        return get_station_config_tool(station_id)

    @mcp.tool
    def list_instrument_types() -> list[str]:
        """List available instrument types in the library.

        Returns instrument type names that can be used with get_instrument_library.
        Examples: dmm, psu, scope, eload
        """
        return list_instrument_types_tool()

    @mcp.tool
    def get_instrument_library(instrument_type: str) -> dict[str, Any]:
        """Get instrument definition including capabilities.

        Args:
            instrument_type: Type name (e.g., "dmm", "psu", "scope")

        Returns:
            Instrument definition with capabilities, SCPI commands, etc.
        """
        return get_instrument_library_tool(instrument_type)

    @mcp.tool
    def list_sequences() -> list[dict[str, Any]]:
        """List available test sequences.

        Returns sequence IDs, names, and descriptions.
        """
        return list_sequences_tool()

    @mcp.tool
    def get_test_templates() -> list[dict[str, Any]]:
        """Get example test code patterns for reference.

        Returns example test functions showing common patterns:
        - Basic measurement test
        - Parametrized test
        - Test with limits from spec
        - Test with operator dialog
        """
        return get_test_templates_tool()

    # -----------------------------------------------------------------------------
    # Matching Tools (deterministic)
    # -----------------------------------------------------------------------------

    @mcp.tool
    def derive_required_capabilities(product_id: str) -> list[dict[str, Any]]:
        """Derive instrument capability requirements from product characteristics.

        Uses direction pairing:
        - DUT OUTPUT → Instrument INPUT (measure what DUT provides)
        - DUT INPUT → Instrument OUTPUT (source what DUT needs)

        Args:
            product_id: The product ID to analyze

        Returns:
            List of required capabilities with direction, domain, signal_types,
            and which characteristic each came from.
        """
        return derive_required_capabilities_tool(product_id)

    @mcp.tool
    def find_compatible_stations(product_id: str) -> list[dict[str, Any]]:
        """Find stations that can test the given product.

        Args:
            product_id: The product ID to find stations for

        Returns:
            List of stations with compatibility status and match details.
        """
        return find_compatible_stations_tool(product_id)

    @mcp.tool
    def check_station_compatibility(product_id: str, station_id: str) -> dict[str, Any]:
        """Check if a specific station can test a specific product.

        Args:
            product_id: The product ID
            station_id: The station ID

        Returns:
            Detailed match report showing what's covered, what's missing,
            and which instruments provide which capabilities.
        """
        return check_station_compatibility_tool(product_id, station_id)

    # -----------------------------------------------------------------------------
    # Write Tools
    # -----------------------------------------------------------------------------

    @mcp.tool
    def validate_product_spec(spec: dict[str, Any]) -> dict[str, Any]:
        """Validate a product specification without saving.

        Args:
            spec: Product spec dict with product, characteristics, test_requirements

        Returns:
            Validation result with success/failure and any errors.
        """
        return validate_product_spec_tool(spec)

    @mcp.tool
    def save_product_spec(product_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Validate and save a product specification.

        Args:
            product_id: ID for the product (used as filename)
            spec: Product spec dict with product, characteristics, test_requirements

        Returns:
            Result with path to saved file or validation errors.
        """
        return save_product_spec_tool(product_id, spec)

    @mcp.tool
    def save_instrument_library(
        instrument_type: str, spec: dict[str, Any]
    ) -> dict[str, Any]:
        """Save a new instrument type definition.

        Args:
            instrument_type: Type name (e.g., "dmm_high_current")
            spec: Instrument spec with instrument metadata and capabilities

        Returns:
            Result with path to saved file.
        """
        return save_instrument_library_tool(instrument_type, spec)

    @mcp.tool
    def validate_test_sequence(sequence: dict[str, Any]) -> dict[str, Any]:
        """Validate a test sequence without saving.

        Args:
            sequence: Sequence dict with sequence metadata and steps

        Returns:
            Validation result with success/failure and any errors.
        """
        return validate_test_sequence_tool(sequence)

    @mcp.tool
    def save_test_sequence(
        sequence_id: str, sequence: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate and save a test sequence.

        Args:
            sequence_id: ID for the sequence (used as filename)
            sequence: Sequence dict with sequence metadata and steps

        Returns:
            Result with path to saved file or validation errors.
        """
        return save_test_sequence_tool(sequence_id, sequence)

    @mcp.tool
    def save_test_file(path: str, code: str) -> dict[str, Any]:
        """Save a Python test file.

        Args:
            path: Relative path under tests/ (e.g., "test_product/test_basic.py")
            code: Python source code for the test file

        Returns:
            Result with absolute path to saved file.
        """
        return save_test_file_tool(path, code)

    # -----------------------------------------------------------------------------
    # Execution Tools
    # -----------------------------------------------------------------------------

    @mcp.tool
    def dry_run_sequence(sequence_id: str) -> dict[str, Any]:
        """Expand a sequence to see what tests would run without executing.

        Args:
            sequence_id: The sequence ID to expand

        Returns:
            List of tests that would be executed, in order.
        """
        return dry_run_sequence_tool(sequence_id)

    @mcp.tool
    def run_sequence(
        sequence_id: str, dut_serial: str, station_id: str
    ) -> dict[str, Any]:
        """Start a test sequence run.

        Args:
            sequence_id: The sequence to run
            dut_serial: Serial number of the device under test
            station_id: Which station to run on

        Returns:
            Run info with run_id for tracking progress.
        """
        return run_sequence_tool(sequence_id, dut_serial, station_id)

    @mcp.tool
    def get_run_status(run_id: str) -> dict[str, Any]:
        """Get status of a test run.

        Args:
            run_id: The run ID returned from run_sequence

        Returns:
            Run status including progress, outcome, and any errors.
        """
        return get_run_status_tool(run_id)

    # -----------------------------------------------------------------------------
    # Product Folder Tools (workflow state management)
    # -----------------------------------------------------------------------------

    @mcp.tool
    def create_product_folder(
        product_id: str,
        name: str,
        description: str | None = None,
        datasheet_content: str | None = None,
    ) -> dict[str, Any]:
        """Create a new product folder with workflow manifest.

        This is the starting point for the datasheet-to-test workflow.
        Creates a folder structure for tracking the product through all steps.

        Args:
            product_id: Unique identifier (e.g., "tps54302")
            name: Human-readable name (e.g., "TPS54302 3A Buck Converter")
            description: Optional description
            datasheet_content: Optional datasheet content to save immediately

        Returns:
            Folder info including path and initial workflow state.
        """
        return create_product_folder_tool(
            product_id, name, description, datasheet_content
        )

    @mcp.tool
    def get_product_folder(product_id: str) -> dict[str, Any]:
        """Get product folder info and workflow state.

        Use this to check where a product is in the workflow and what
        files have been created.

        Args:
            product_id: The product ID

        Returns:
            Folder info including workflow state, file references, and progress.
        """
        return get_product_folder_tool(product_id)

    @mcp.tool
    def list_product_folders() -> list[dict[str, Any]]:
        """List all product folders and their workflow states.

        Returns:
            List of products with IDs, names, current step, and progress.
        """
        return list_product_folders_tool()

    @mcp.tool
    def complete_workflow_step(
        product_id: str,
        step: str,
        agent: str | None = None,
        confidence: float | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Mark a workflow step as completed and advance to next step.

        Valid steps in order:
        - parse_datasheet: Extract characteristics from datasheet
        - review_spec: Human review/edit of product spec
        - derive_requirements: Get instrument requirements (deterministic)
        - select_station: Choose compatible station
        - generate_tests: Create pytest test code
        - execute_analyze: Run tests and analyze results

        Args:
            product_id: The product ID
            step: The step to complete
            agent: Optional agent name (e.g., "claude")
            confidence: Optional confidence score (0.0-1.0)
            notes: Optional notes about the completion

        Returns:
            Updated workflow state with next step.
        """
        return complete_workflow_step_tool(product_id, step, agent, confidence, notes)

    @mcp.tool
    def save_product_spec_to_folder(
        product_id: str, spec: dict[str, Any]
    ) -> dict[str, Any]:
        """Save a product spec to an existing product folder.

        The spec should have this structure:
        {
            "product": {"id": "...", "name": "..."},
            "characteristics": {...},
            "test_requirements": {...}
        }

        Args:
            product_id: The product ID (folder must exist)
            spec: Product spec dict

        Returns:
            Path to saved spec file.
        """
        return save_product_spec_to_folder_tool(product_id, spec)

    @mcp.tool
    def get_editor_url(
        resource_type: str, resource_id: str, base_url: str = "http://localhost:8000"
    ) -> dict[str, Any]:
        """Get URL to open the UI editor for detailed editing.

        Use this when the user wants to review or edit a resource
        in a visual interface.

        Args:
            resource_type: "product", "station", or "results"
            resource_id: ID of the resource
            base_url: UI server URL (default: http://localhost:8000)

        Returns:
            URL to open in browser.
        """
        return get_editor_url_tool(resource_type, resource_id, base_url)

    # -----------------------------------------------------------------------------
    # Fixture Tools
    # -----------------------------------------------------------------------------

    @mcp.tool
    def list_fixtures() -> list[dict[str, Any]]:
        """List all available fixture configurations.

        Fixtures define pin-to-instrument mappings for testing products.
        They bridge product pins to station instruments.

        Returns:
            List of fixtures with id, name, product info, and point count.
        """
        return list_fixtures_tool()

    @mcp.tool
    def get_fixture_config(fixture_id: str) -> dict[str, Any]:
        """Get fixture configuration by ID.

        Args:
            fixture_id: The fixture ID

        Returns:
            Full fixture config including all pin mapping points.
        """
        return get_fixture_config_tool(fixture_id)

    @mcp.tool
    def validate_fixture_config(config: dict[str, Any]) -> dict[str, Any]:
        """Validate a fixture configuration without saving.

        Args:
            config: Fixture config with fixture and points sections

        Returns:
            Validation result with success/failure and any errors.
        """
        return validate_fixture_config_tool(config)

    @mcp.tool
    def save_fixture_config(
        fixture_id: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate and save a fixture configuration.

        Args:
            fixture_id: ID for the fixture (used as filename)
            config: Fixture config with fixture and points sections

        Returns:
            Result with path to saved file or validation errors.
        """
        return save_fixture_config_tool(fixture_id, config)

    @mcp.tool
    def get_fixtures_for_product(product_id: str) -> list[dict[str, Any]]:
        """Find fixtures compatible with a product.

        Searches by product_id match or product_family pattern.

        Args:
            product_id: The product ID to find fixtures for

        Returns:
            List of matching fixtures with match type.
        """
        return get_fixtures_for_product_tool(product_id)

    @mcp.tool
    def get_compatible_stations_for_fixture(fixture_id: str) -> list[dict[str, Any]]:
        """Find stations that have all instruments required by a fixture.

        Args:
            fixture_id: The fixture ID

        Returns:
            List of stations with compatibility info and missing instruments.
        """
        return get_compatible_stations_for_fixture_tool(fixture_id)

    return mcp


def run_mcp_server():
    """Run the MCP server (for CLI entry point)."""
    mcp = create_mcp_server()
    mcp.run()
