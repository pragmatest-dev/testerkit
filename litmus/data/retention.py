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


def _is_project_owned(path: Path) -> bool:
    """Return True if *path* is owned by the current project.

    A directory is project-owned if:
    1. It was explicitly set via ``results_dir`` in ``litmus.yaml``, OR
    2. It is located under the project repo folder (CWD ancestors)

    Anything else (global default, arbitrary paths) is not owned.
    """
    resolved = path.resolve()

    # Check if project explicitly defines results_dir
    try:
        from litmus.connect import _find_project_config

        found = _find_project_config()
        if found:
            root, project = found
            if project.results_dir:
                explicit = (root / project.results_dir).resolve()
                try:
                    resolved.relative_to(explicit)
                    return True
                except ValueError:
                    pass
            # Check if under the project repo folder
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                pass
    except (ImportError, OSError):
        pass

    return False


def prune_date_dirs(base_dir: Path, cutoff: date, *, dry_run: bool = False) -> list[Path]:
    """Delete date-named subdirectories older than *cutoff*.

    Only directories whose name is a valid ISO date (YYYY-MM-DD) and
    predates *cutoff* are removed. Non-date directories are left untouched.

    Raises:
        PermissionError: If *base_dir* is not owned by the current project.

    Returns:
        List of directories that were (or would be) deleted.
    """
    if not _is_project_owned(base_dir):
        raise PermissionError(
            f"Refusing to prune: {base_dir}\n"
            "Only project-owned directories can be pruned (results_dir in litmus.yaml "
            "or under the project repo folder)."
        )
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
    data_types: tuple[str, ...] = ("channels", "events"),
    dry_run: bool = False,
    materialize: bool = True,
) -> dict[str, list[Path]]:
    """Prune date-partitioned subdirectories under *results_dir*.

    Args:
        results_dir: Root results directory.
        older_than: Duration string (e.g. '30d').
        data_types: Which subdirectories to prune.
        dry_run: If True, report but don't delete.
        materialize: If True (default), copy channel data referenced by
            parquet files into ``_ref/`` sidecar dirs before pruning channels.

    Raises:
        PermissionError: If *results_dir* is the shared global results
            directory. Projects cannot prune shared data.

    Returns:
        Dict mapping subdirectory name to list of pruned date dirs.
    """
    if not _is_project_owned(results_dir):
        raise PermissionError(
            f"Refusing to prune: {results_dir}\n"
            "Only project-owned directories can be pruned (results_dir in litmus.yaml "
            "or under the project repo folder)."
        )
    delta = parse_duration(older_than)
    cutoff = date.today() - delta
    result: dict[str, list[Path]] = {}

    # Materialize channel refs before pruning channel data
    if materialize and "channels" in data_types:
        dirs_to_prune = prune_date_dirs(results_dir / "channels", cutoff, dry_run=True)
        if dirs_to_prune:
            from litmus.data.materialize import materialize_channel_refs

            materialize_channel_refs(results_dir, dirs_to_prune)

    for subdir in data_types:
        result[subdir] = prune_date_dirs(results_dir / subdir, cutoff, dry_run=dry_run)
    return result
