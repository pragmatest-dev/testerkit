"""Data retention utilities for date-partitioned result directories."""

from __future__ import annotations

import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.schemas import OutputConfig

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


def prune_all(
    results_dir: Path,
    older_than: str,
    *,
    data_types: tuple[str, ...] = ("channels", "sessions", "events"),
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Prune date-partitioned subdirectories under *results_dir*.

    Args:
        results_dir: Root results directory.
        older_than: Duration string (e.g. '30d').
        data_types: Which subdirectories to prune.
        dry_run: If True, report but don't delete.

    Returns:
        Dict mapping subdirectory name to list of pruned date dirs.
    """
    delta = parse_duration(older_than)
    cutoff = date.today() - delta
    result: dict[str, list[Path]] = {}
    for subdir in data_types:
        result[subdir] = prune_date_dirs(results_dir / subdir, cutoff, dry_run=dry_run)
    return result


def prune_from_config(
    project_dir: Path,
    outputs: list[OutputConfig],
    *,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Prune using per-output retention settings from OutputConfig entries.

    Args:
        project_dir: Project root directory (output_dir paths are relative to this).
        outputs: List of OutputConfig instances with optional ``retention`` field.
        dry_run: If True, report but don't delete.

    Returns:
        Dict mapping output format to list of pruned date dirs.
    """
    result: dict[str, list[Path]] = {}
    for output_cfg in outputs:
        retention = output_cfg.retention
        if not retention:
            continue
        output_dir = output_cfg.default_output_dir()
        base_dir = project_dir / output_dir
        delta = parse_duration(retention)
        cutoff = date.today() - delta
        label = output_cfg.format or output_dir
        result[label] = prune_date_dirs(base_dir, cutoff, dry_run=dry_run)
    return result
