"""Centralized search path management for TesterKit resources.

This module provides a single source of truth for where different
resource types (parts, stations, instruments, etc.) are located.

All paths are relative to the project root (defaults to cwd).
Run the server from the project directory: `cd myproject && testerkit serve`
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class ResourceType(StrEnum):
    """Types of resources in the TesterKit ecosystem."""

    PARTS = "parts"  # Part YAML files
    STATIONS = "stations"  # Station configurations
    INSTRUMENTS = "instruments"  # Instrument library definitions
    FIXTURES = "fixtures"  # Test fixture definitions
    TESTS = "tests"  # Test configurations


def _resolve_root(project_root: Path | None = None) -> Path:
    """Resolve project root, defaulting to cwd."""
    return project_root if project_root is not None else Path.cwd()


def get_search_paths(
    resource_type: ResourceType,
    project_root: Path | None = None,
) -> list[Path]:
    """Get search paths for a resource type.

    Args:
        resource_type: Type of resource to find paths for.
        project_root: Project root directory. Defaults to cwd.

    Returns:
        List containing the single path for this resource type, if it exists.
    """
    path = _resolve_root(project_root) / resource_type.value
    if path.is_dir():
        return [path]
    return []


def get_all_search_paths(
    project_root: Path | None = None,
) -> dict[ResourceType, list[Path]]:
    """Get search paths for all resource types."""
    return {rt: get_search_paths(rt, project_root) for rt in ResourceType}


# Convenience aliases
def get_part_paths(project_root: Path | None = None) -> list[Path]:
    """Get search paths for part folders."""
    return get_search_paths(ResourceType.PARTS, project_root)


def get_station_paths(project_root: Path | None = None) -> list[Path]:
    """Get search paths for station configurations."""
    return get_search_paths(ResourceType.STATIONS, project_root)


def get_instrument_paths(project_root: Path | None = None) -> list[Path]:
    """Get search paths for instrument library."""
    return get_search_paths(ResourceType.INSTRUMENTS, project_root)


def get_fixture_paths(project_root: Path | None = None) -> list[Path]:
    """Get search paths for test fixtures."""
    return get_search_paths(ResourceType.FIXTURES, project_root)
