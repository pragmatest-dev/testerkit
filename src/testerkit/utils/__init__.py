"""TesterKit utilities.

Import directly from the submodule:

    from testerkit.utils.ranges import expand_range, expand_numeric_range
    from testerkit.utils.paths import get_search_paths, get_instrument_paths
"""

from datetime import datetime


def local_time(iso_timestamp: str, fmt: str = "%H:%M:%S") -> str:
    """Convert an ISO 8601 UTC timestamp to a local time string."""
    try:
        return datetime.fromisoformat(iso_timestamp).astimezone().strftime(fmt)
    except (ValueError, TypeError):
        return iso_timestamp
