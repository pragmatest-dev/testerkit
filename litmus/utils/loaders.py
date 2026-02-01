"""Shared YAML loading and parsing utilities.

This module provides DRY utilities for common patterns used across
the litmus codebase for loading YAML configuration files.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from litmus.capabilities.models import Direction, Domain, SignalType


def find_yaml_files(
    search_paths: list[Path],
    *,
    prefix_skip: str = "_",
    pattern: str = "*.yaml",
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Iterate over YAML files in search paths, loading their contents.

    Args:
        search_paths: List of directories to search.
        prefix_skip: Skip files starting with this prefix (default "_").
        pattern: Glob pattern for files (default "*.yaml").

    Yields:
        Tuples of (file_path, parsed_data) for each valid YAML file.
        Files that fail to parse are silently skipped.

    Example:
        >>> for path, data in find_yaml_files([Path("products/"), Path("demo/products/")]):
        ...     print(path.stem, data.get("id"))
    """
    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        for yaml_file in search_dir.glob(pattern):
            if prefix_skip and yaml_file.name.startswith(prefix_skip):
                continue
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                    if data is not None:
                        yield yaml_file, data
            except Exception:
                # Skip files that fail to parse
                continue


def load_yaml_file(path: Path) -> dict[str, Any] | None:
    """Load a single YAML file safely.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed YAML data as dict, or None if file doesn't exist or fails to parse.
    """
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def parse_capability_enums(
    direction_str: str,
    domain_str: str,
    signal_types_raw: list[str] | None = None,
) -> tuple[Direction, Domain, list[SignalType]]:
    """Parse capability enum values from YAML strings.

    Args:
        direction_str: Direction string (e.g., "input", "output", "bidir").
        domain_str: Domain string (e.g., "voltage", "current").
        signal_types_raw: List of signal type strings (e.g., ["dc", "ac"]).

    Returns:
        Tuple of (Direction, Domain, list[SignalType]).

    Raises:
        ValueError: If any enum value is invalid.

    Example:
        >>> direction, domain, signal_types = parse_capability_enums(
        ...     "output", "voltage", ["dc"]
        ... )
    """
    # Import here to avoid circular import
    from litmus.capabilities.models import Direction, Domain, SignalType

    direction = Direction(direction_str.lower())
    domain = Domain(domain_str.lower())
    signal_types = [
        SignalType(st.lower()) for st in (signal_types_raw or [])
    ]
    return direction, domain, signal_types


def find_or_create_path(
    resource_id: str,
    search_dirs: list[Path],
    filename: str | None = None,
) -> Path | None:
    """Find an existing file or determine where to create a new one.

    Searches for existing files first. If not found, returns path in
    the first existing directory.

    Args:
        resource_id: ID of the resource (used as filename if filename not provided).
        search_dirs: List of directories to search.
        filename: Optional explicit filename (default: "{resource_id}.yaml").

    Returns:
        Path to existing file, or path where new file should be created,
        or None if no valid directory exists.

    Example:
        >>> path = find_or_create_path("my_product", [Path("products/"), Path("demo/products/")])
    """
    if filename is None:
        filename = f"{resource_id}.yaml"

    # First pass: look for existing file
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        existing = search_dir / filename
        if existing.exists():
            return existing

    # Second pass: find first valid directory for new file
    for search_dir in search_dirs:
        if search_dir.exists():
            return search_dir / filename

    return None
