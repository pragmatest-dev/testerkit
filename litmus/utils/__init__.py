"""Litmus utilities.

This package provides shared utilities for the litmus codebase:

- ranges: Range expansion for pins, channels, and numeric values
- paths: Centralized search path management
- time: Timestamp formatting
"""

from datetime import datetime

from litmus.utils.paths import (
    ResourceType,
    get_fixture_paths,
    get_instrument_paths,
    get_product_paths,
    get_search_paths,
    get_sequence_paths,
    get_station_paths,
)
from litmus.utils.ranges import (
    expand_numeric_range,
    expand_range,
    generate_numeric_range,
)


def local_time(iso_timestamp: str, fmt: str = "%H:%M:%S") -> str:
    """Convert an ISO 8601 UTC timestamp to a local time string.

    Args:
        iso_timestamp: e.g. ``"2026-03-07T20:07:43.123456+00:00"``
        fmt: strftime format string. Default ``"%H:%M:%S"``.

    Returns:
        Formatted local time string, or the raw input on parse failure.
    """
    try:
        return datetime.fromisoformat(iso_timestamp).astimezone().strftime(fmt)
    except (ValueError, TypeError):
        return iso_timestamp


__all__ = [
    # time
    "local_time",
    # ranges
    "expand_range",
    "expand_numeric_range",
    "generate_numeric_range",
    # paths
    "ResourceType",
    "get_search_paths",
    "get_product_paths",
    "get_station_paths",
    "get_instrument_paths",
    "get_sequence_paths",
    "get_fixture_paths",
]
