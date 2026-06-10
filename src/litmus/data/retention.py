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
    1. It was explicitly set via ``data_dir`` in ``litmus.yaml``, OR
    2. It is located under the project repo folder (CWD ancestors)

    Anything else (global default, arbitrary paths) is not owned.
    """
    resolved = path.resolve()

    # Check if project explicitly defines data_dir
    try:
        from litmus.connect import _find_project_config

        found = _find_project_config()
        if found:
            root, project = found
            if project.data_dir:
                explicit = (root / project.data_dir).resolve()
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
            "Only project-owned directories can be pruned (data_dir in litmus.yaml "
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


_SEG_RE = re.compile(r"^(.+)_([0-9a-f]{8})(?:_\d+)?$")


def _referenced_pairs(data_dir: Path, session_shorts: set[str]) -> set[tuple[str, str]]:
    """``(channel_id, session_short)`` pairs any run references, via the runs index.

    Reference-aware retention asks the runs store *which channel slices are
    evidence* (the lakehouse VACUUM reachability query) rather than copying them
    out. No cross-store file read — the runs daemon answers from its index.
    """
    if not session_shorts or not (data_dir / "runs").is_dir():
        return set()
    from litmus.data.run_store import RunStore

    store = RunStore(_data_dir=data_dir)
    try:
        refs = store.find_channel_refs(session_shorts)
    finally:
        store.close()
    return {(r["channel_id"], r["session_short"]) for r in refs}


def _prune_channels_ref_aware(data_dir: Path, cutoff: date, *, dry_run: bool) -> list[Path]:
    """Prune *unreferenced* channel segments older than *cutoff*; pin referenced ones.

    Reference-aware retention (the lakehouse VACUUM model): a channel slice a run
    references is evidence and is kept — its ``channel://`` ref stays valid, no
    copy. Unreferenced slices age out. Replaces the old copy-on-prune
    (``materialize_channel_refs``), which duplicated referenced data into FileStore.
    Returns the segment files removed (a now-empty date dir is removed too).
    """
    channels_dir = data_dir / "channels"
    if not _is_project_owned(channels_dir):
        raise PermissionError(
            f"Refusing to prune: {channels_dir}\n"
            "Only project-owned directories can be pruned (data_dir in litmus.yaml "
            "or under the project repo folder)."
        )
    removed: list[Path] = []
    if not channels_dir.is_dir():
        return removed

    # Candidate segments in old date dirs, parsed to (channel_id, session_short).
    old: dict[Path, list[tuple[Path, str, str]]] = {}
    for child in sorted(channels_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            if date.fromisoformat(child.name) >= cutoff:
                continue
        except ValueError:
            continue
        segs: list[tuple[Path, str, str]] = []
        for seg in sorted(child.glob("*.arrow")):
            m = _SEG_RE.match(seg.stem)
            if m:
                segs.append((seg, m.group(1), m.group(2)))
        old[child] = segs

    if not old:
        return removed

    session_shorts = {sess for segs in old.values() for (_, _, sess) in segs}
    referenced = _referenced_pairs(data_dir, session_shorts)

    for date_dir, segs in old.items():
        for seg, cid, sess in segs:
            if (cid, sess) in referenced:
                continue  # pinned — a run references this slice (evidence)
            removed.append(seg)
            if not dry_run:
                seg.unlink(missing_ok=True)
        # Drop the date dir only if every segment was unreferenced + pruned.
        if not dry_run and date_dir.is_dir() and not any(date_dir.iterdir()):
            date_dir.rmdir()
    return removed


def prune_all(
    data_dir: Path,
    older_than: str,
    *,
    data_types: tuple[str, ...] = ("channels", "events"),
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Prune date-partitioned data under *data_dir*, reference-aware for channels.

    Channels use reference-aware retention: a channel slice a run references is
    pinned (kept; its ``channel://`` ref stays valid — no copy), unreferenced
    slices age out. Other data types (events) prune whole date dirs older than the
    cutoff. Runs are never pruned here — they are the durable record.

    Args:
        data_dir: Root results directory.
        older_than: Duration string (e.g. '30d').
        data_types: Which subdirectories to prune.
        dry_run: If True, report but don't delete.

    Raises:
        PermissionError: If *data_dir* is not project-owned.

    Returns:
        Dict mapping subdirectory name to the list of pruned paths (segment files
        for channels; date dirs for others).
    """
    if not _is_project_owned(data_dir):
        raise PermissionError(
            f"Refusing to prune: {data_dir}\n"
            "Only project-owned directories can be pruned (data_dir in litmus.yaml "
            "or under the project repo folder)."
        )
    cutoff = date.today() - parse_duration(older_than)
    result: dict[str, list[Path]] = {}
    for subdir in data_types:
        if subdir == "channels":
            result["channels"] = _prune_channels_ref_aware(data_dir, cutoff, dry_run=dry_run)
        else:
            result[subdir] = prune_date_dirs(data_dir / subdir, cutoff, dry_run=dry_run)
    return result
