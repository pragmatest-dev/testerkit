"""Centralized search path management for Litmus resources.

This module provides a single source of truth for where different
resource types (products, stations, instruments, etc.) are located.

All paths are relative to the current working directory (project root).
Run the server from the project directory: `cd myproject && litmus serve`
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class ResourceType(StrEnum):
    """Types of resources in the Litmus ecosystem."""

    PRODUCTS = "products"  # Product folders with spec.yaml
    STATIONS = "stations"  # Station configurations
    INSTRUMENTS = "instruments"  # Instrument library definitions
    SEQUENCES = "sequences"  # Test sequences
    FIXTURES = "fixtures"  # Test fixture definitions
    TESTS = "tests"  # Test configurations


def get_search_paths(resource_type: ResourceType) -> list[Path]:
    """Get search paths for a resource type.

    Paths are relative to the current working directory (project root).

    Args:
        resource_type: Type of resource to find paths for.

    Returns:
        List containing the single path for this resource type, if it exists.
    """
    path = Path.cwd() / resource_type.value
    if path.is_dir():
        return [path]
    return []


def get_all_search_paths() -> dict[ResourceType, list[Path]]:
    """Get search paths for all resource types.

    Returns:
        Dictionary mapping ResourceType to list of paths.
    """
    return {rt: get_search_paths(rt) for rt in ResourceType}


# Convenience aliases
def get_product_paths() -> list[Path]:
    """Get search paths for product folders."""
    return get_search_paths(ResourceType.PRODUCTS)


def get_station_paths() -> list[Path]:
    """Get search paths for station configurations."""
    return get_search_paths(ResourceType.STATIONS)


def get_instrument_paths() -> list[Path]:
    """Get search paths for instrument library."""
    return get_search_paths(ResourceType.INSTRUMENTS)


def get_sequence_paths() -> list[Path]:
    """Get search paths for test sequences."""
    return get_search_paths(ResourceType.SEQUENCES)


def get_fixture_paths() -> list[Path]:
    """Get search paths for test fixtures."""
    return get_search_paths(ResourceType.FIXTURES)
