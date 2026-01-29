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
    derive_required_capabilities_tool,
    dry_run_sequence_tool,
    find_compatible_stations_tool,
    get_instrument_library_tool,
    get_product_spec_tool,
    get_run_status_tool,
    get_station_config_tool,
    get_test_templates_tool,
    list_instrument_types_tool,
    list_products_tool,
    list_sequences_tool,
    list_stations_tool,
    run_sequence_tool,
    save_instrument_library_tool,
    save_product_spec_tool,
    save_test_file_tool,
    save_test_sequence_tool,
    validate_product_spec_tool,
    validate_test_sequence_tool,
)


def create_mcp_server() -> FastMCP:
    """Create and configure the Litmus MCP server."""
    mcp = FastMCP(
        "Litmus",
        instructions="""Litmus is a hardware test platform. Use these tools to:

1. EXPLORE: List and read products, stations, instruments, sequences
2. MATCH: Find which stations can test which products (deterministic capability matching)
3. CREATE: Save new product specs, instrument definitions, test sequences, test code
4. EXECUTE: Run tests and check results

Typical workflow:
- Parse datasheet → save_product_spec
- derive_required_capabilities → find_compatible_stations
- If no stations, create new instruments with save_instrument_library
- Generate test code with save_test_file
- Generate sequence with save_test_sequence
- dry_run_sequence to validate
- run_sequence to execute
""",
    )

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

    return mcp


def run_mcp_server():
    """Run the MCP server (for CLI entry point)."""
    mcp = create_mcp_server()
    mcp.run()
