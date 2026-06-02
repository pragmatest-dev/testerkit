"""Copy-on-prune: materialize channel:// refs before channel data is deleted.

When channel data (Arrow IPC files) is pruned before parquet test runs,
``channel://`` URIs in parquet ``out_*`` columns would break. This module
copies referenced channel data into FileStore as ``.arrow`` IPC files
and rewrites the parquet with ``file://{session_id}/{filename}`` URIs.

Build item 1d: previously this wrote to a per-parquet ``_ref/`` sidecar
directory with ``file://_ref/{filename}`` URIs. Post-1d, all artifacts
land in one canonical home (FileStore at ``files/{date}/{session_id}/``)
so a session's blobs + materialized channel data live together.

Uses RunStore for all parquet access — no direct file scanning.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pyarrow as pa


def materialize_channel_refs(data_dir: Path, channel_dirs_to_prune: list[Path]) -> int:
    """Materialize channel:// refs in parquet files before channel pruning.

    Queries RunStore (DuckDB index) to find channel refs, reads channel
    data via ChannelStore API, writes materialized data into FileStore
    (one canonical home — item 1d), then rewrites parquet files with
    ``file://{session_id}/{filename}`` URIs via RunStore.

    Args:
        data_dir: Root results directory (contains ``runs/`` and
            ``channels/`` subdirs).
        channel_dirs_to_prune: Channel date directories about to be deleted.

    Returns:
        Count of materialized references.
    """
    from litmus.data.channels.store import ChannelStore
    from litmus.data.files import get_filestore
    from litmus.data.run_store import RunStore

    pruning = ChannelStore.list_channel_refs(channel_dirs_to_prune)
    if not pruning:
        return 0

    session_shorts = {s for _, s in pruning}

    # Read-only ChannelStore for querying (no active session writes).
    # Dummy session_id — we only use store.query() which reads from disk.
    store = ChannelStore(data_dir, session_id=UUID(int=0))

    runs_dir = data_dir / "runs"
    if not runs_dir.is_dir():
        return 0

    run_store = RunStore(_data_dir=data_dir)
    filestore = get_filestore()

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

            for ref in file_refs:
                channel_id = ref["channel_id"]
                session_short = ref["session_short"]
                # The full session_id comes from the URI's ``?session=``
                # query parameter, captured at parquet-write time and
                # surfaced through the daemon's ``measurement_refs`` index
                # (item 1d). Fall back to session_short when an older
                # parquet predates the session_id column.
                session_id = ref.get("session_id") or session_short
                key = (channel_id, session_short)

                if key not in cache:
                    cache[key] = store.query(
                        channel_id,
                        session_id=session_short,
                    )

                # Item 1d: write into FileStore (one canonical home)
                # via the Arrow Table serializer registered in
                # C6-remainder. URI is ``file://{session_id}/{filename}``.
                new_uri = filestore.write(
                    channel_id,
                    cache[key],
                    session_id=session_id,
                )
                replacements.setdefault(ref["col_name"], {})[ref["row_idx"]] = new_uri
                count += 1

            run_store.rewrite_refs(Path(file_path), replacements)

        return count
    finally:
        run_store.close()
