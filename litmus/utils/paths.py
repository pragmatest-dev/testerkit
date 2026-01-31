"""Centralized search path management for Litmus resources.

This module provides a single source of truth for where different
resource types (products, stations, instruments, etc.) are located.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class ResourceType(StrEnum):
    """Types of resources in the Litmus ecosystem."""

    PRODUCTS = "products"  # Product folders with spec.yaml
    SPECS = "specs"  # Legacy flat spec files
    STATIONS = "stations"  # Station configurations
    INSTRUMENTS = "instruments"  # Instrument library definitions
    SEQUENCES = "sequences"  # Test sequences
    FIXTURES = "fixtures"  # Test fixture definitions
    TESTS = "tests"  # Test configurations


def get_search_paths(
    resource_type: ResourceType,
    *,
    include_demo: bool = True,
    include_builtin: bool = True,
) -> list[Path]:
    """Get search paths for a resource type.

    Args:
        resource_type: Type of resource to find paths for.
        include_demo: Include demo/ subdirectory paths (default True).
        include_builtin: Include built-in library paths for instruments (default True).

    Returns:
        List of Path objects in priority order (user paths first).

    Example:
        >>> paths = get_search_paths(ResourceType.PRODUCTS)
        >>> for path, data in find_yaml_files(paths):
        ...     print(path)
    """
    cwd = Path.cwd()
    paths: list[Path] = []

    if resource_type == ResourceType.PRODUCTS:
        paths.append(cwd / "products")
        if include_demo:
            paths.append(cwd / "demo" / "products")

    elif resource_type == ResourceType.SPECS:
        # Legacy flat spec structure
        paths.append(cwd / "specs")
        if include_demo:
            paths.append(cwd / "demo" / "specs")

    elif resource_type == ResourceType.STATIONS:
        paths.append(cwd / "stations")
        if include_demo:
            paths.append(cwd / "demo" / "stations")

    elif resource_type == ResourceType.INSTRUMENTS:
        # User instruments first
        paths.append(cwd / "instruments")
        if include_demo:
            paths.append(cwd / "demo" / "instruments")
        # Built-in library as fallback
        if include_builtin:
            paths.append(Path(__file__).parent.parent / "instruments" / "library")

    elif resource_type == ResourceType.SEQUENCES:
        paths.append(cwd / "sequences")
        if include_demo:
            paths.append(cwd / "demo" / "sequences")

    elif resource_type == ResourceType.FIXTURES:
        paths.append(cwd / "fixtures")
        if include_demo:
            paths.append(cwd / "demo" / "fixtures")

    elif resource_type == ResourceType.TESTS:
        paths.append(cwd / "tests")
        if include_demo:
            paths.append(cwd / "demo" / "tests")

    return paths


def get_all_search_paths(
    *,
    include_demo: bool = True,
    include_builtin: bool = True,
) -> dict[ResourceType, list[Path]]:
    """Get search paths for all resource types.

    Args:
        include_demo: Include demo/ subdirectory paths.
        include_builtin: Include built-in library paths.

    Returns:
        Dictionary mapping ResourceType to list of paths.
    """
    return {
        rt: get_search_paths(rt, include_demo=include_demo, include_builtin=include_builtin)
        for rt in ResourceType
    }


# Convenience aliases for common path combinations
def get_product_paths(include_demo: bool = True) -> list[Path]:
    """Get search paths for product folders."""
    return get_search_paths(ResourceType.PRODUCTS, include_demo=include_demo)


def get_station_paths(include_demo: bool = True) -> list[Path]:
    """Get search paths for station configurations."""
    return get_search_paths(ResourceType.STATIONS, include_demo=include_demo)


def get_instrument_paths(include_demo: bool = True, include_builtin: bool = True) -> list[Path]:
    """Get search paths for instrument library."""
    return get_search_paths(
        ResourceType.INSTRUMENTS, include_demo=include_demo, include_builtin=include_builtin
    )


def get_sequence_paths(include_demo: bool = True) -> list[Path]:
    """Get search paths for test sequences."""
    return get_search_paths(ResourceType.SEQUENCES, include_demo=include_demo)


def get_fixture_paths(include_demo: bool = True) -> list[Path]:
    """Get search paths for test fixtures."""
    return get_search_paths(ResourceType.FIXTURES, include_demo=include_demo)
