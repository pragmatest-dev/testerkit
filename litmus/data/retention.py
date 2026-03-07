"""Data retention utilities for date-partitioned result directories."""

from __future__ import annotations

import re
import shutil
from datetime import date, timedelta
from pathlib import Path

_DURATION_RE = re.compile(r"^(\d+)d$")


def parse_duration(s: str) -> timedelta:
    """Parse a duration string like '30d' into a timedelta.

    Only day-based durations are supported.

    Raises:
        ValueError: If the string doesn't match the expected format.
    """
    m = _DURATION_RE.match(s.strip())
    if not m:
        msg = f"Invalid duration '{s}'. Expected format: '<number>d' (e.g. '30d', '90d')"
        raise ValueError(msg)
    return timedelta(days=int(m.group(1)))


def prune_date_dirs(base_dir: Path, cutoff: date, *, dry_run: bool = False) -> list[Path]:
    """Delete date-named subdirectories older than *cutoff*.

    Only directories whose name is a valid ISO date (YYYY-MM-DD) and
    predates *cutoff* are removed. Non-date directories are left untouched.

    Returns:
        List of directories that were (or would be) deleted.
    """
    removed: list[Path] = []
    if not base_dir.is_dir():
        return removed
    for child in sorted(base_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            dir_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if dir_date < cutoff:
            removed.append(child)
            if not dry_run:
                shutil.rmtree(child)
    return removed


ALL_DATA_TYPES = ("channels", "sessions", "events")


def prune_all(
    results_dir: Path,
    older_than: str,
    *,
    data_types: tuple[str, ...] = ALL_DATA_TYPES,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Prune date-partitioned subdirectories under *results_dir*.

    Args:
        results_dir: Root results directory.
        older_than: Duration string (e.g. '30d').
        data_types: Which subdirectories to prune (default: all).
        dry_run: If True, report but don't delete.

    Returns:
        Dict mapping subdirectory name to list of pruned date dirs.
    """
    invalid = set(data_types) - set(ALL_DATA_TYPES)
    if invalid:
        valid = ", ".join(ALL_DATA_TYPES)
        msg = f"Invalid data type(s): {', '.join(sorted(invalid))}. Valid: {valid}"
        raise ValueError(msg)
    delta = parse_duration(older_than)
    cutoff = date.today() - delta
    result: dict[str, list[Path]] = {}
    for subdir in data_types:
        result[subdir] = prune_date_dirs(results_dir / subdir, cutoff, dry_run=dry_run)
    return result
