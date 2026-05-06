"""Copy-on-prune: materialize channel:// refs before channel data is deleted.

When channel data (Arrow IPC files) is pruned before parquet test runs,
``channel://`` URIs in parquet ``out_*`` columns would break. This module
copies referenced channel data into each parquet's ``_ref/`` sidecar
directory as ``.arrow`` files and rewrites the parquet with ``file://`` URIs.

Uses RunStore for all parquet access — no direct file scanning.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.ipc as ipc

from litmus.data.backends._row_helpers import REF_PATH_PREFIX


def _save_arrow_ref(ref_dir: Path, channel_id: str, session_short: str, table: pa.Table) -> str:
    """Save Arrow IPC table to ref dir, return ``file://`` URI."""
    ref_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{channel_id}_{session_short}.arrow"
    writer = ipc.new_stream(ref_dir / filename, table.schema)
    writer.write_table(table)
    writer.close()
    return f"file://{REF_PATH_PREFIX}{filename}"


def materialize_channel_refs(results_dir: Path, channel_dirs_to_prune: list[Path]) -> int:
    """Materialize channel:// refs in parquet files before channel pruning.

    Queries RunStore (DuckDB index) to find channel refs, reads channel
    data via ChannelStore API, saves materialized data as sidecar files,
    then rewrites parquet files with ``file://`` URIs via RunStore.

    Args:
        results_dir: Root results directory (contains ``runs/`` and
            ``channels/`` subdirs).
        channel_dirs_to_prune: Channel date directories about to be deleted.

    Returns:
        Count of materialized references.
    """
    from litmus.data.channels.store import ChannelStore
    from litmus.data.run_store import RunStore

    pruning = ChannelStore.list_channel_refs(channel_dirs_to_prune)
    if not pruning:
        return 0

    session_shorts = {s for _, s in pruning}

    # Read-only ChannelStore for querying (no active session writes).
    # Dummy session_id — we only use store.query() which reads from disk.
    store = ChannelStore(results_dir, session_id=UUID(int=0))

    runs_dir = results_dir / "runs"
    if not runs_dir.is_dir():
        return 0

    run_store = RunStore(_results_dir=results_dir)

    try:
        # DuckDB query — no file scanning
        refs = run_store.find_channel_refs(session_shorts)
        if not refs:
            return 0

        # Filter to only refs in the pruning set
        refs = [r for r in refs if (r["channel_id"], r["session_short"]) in pruning]

        count = 0
        cache: dict[tuple[str, str], pa.Table] = {}

        # Group by file_path for batch rewriting
        by_file: dict[str, list[dict]] = {}
        for ref in refs:
            by_file.setdefault(ref["file_path"], []).append(ref)

        for file_path, file_refs in by_file.items():
            replacements: dict[str, dict[int, str]] = {}
            ref_dir = RunStore.ref_dir_for(Path(file_path))

            for ref in file_refs:
                channel_id = ref["channel_id"]
                session_short = ref["session_short"]
                key = (channel_id, session_short)

                if key not in cache:
                    cache[key] = store.query(
                        channel_id,
                        session_id=session_short,
                    )

                new_uri = _save_arrow_ref(
                    ref_dir,
                    channel_id,
                    session_short,
                    cache[key],
                )
                replacements.setdefault(ref["col_name"], {})[ref["row_idx"]] = new_uri
                count += 1

            run_store.rewrite_refs(Path(file_path), replacements)

        return count
    finally:
        run_store.close()
