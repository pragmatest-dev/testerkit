"""Runs daemon boot self-heal: a corrupt on-disk ``_index.duckdb`` must not
be a fatal poison-pill.

The index is a DERIVED cache (rebuildable from parquet), so an unreadable
index — a ``kill -9`` mid-write, a bad disk block, a DuckDB storage-format
bump on upgrade — must be discarded and rebuilt rather than crash the daemon
at boot on every respawn. Rationale lives in ``_open_index``'s docstring.

``_open_index`` opens a DuckDB file only (no Flight server, no threads), so
``tmp_path`` is safe here — this is not a daemon-spawning test.
"""

from __future__ import annotations

from pathlib import Path

from litmus.data._runs_duckdb_daemon import _open_index


def test_fresh_then_existing(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"

    conn, is_fresh = _open_index(idx)
    assert is_fresh is True  # file did not exist → fresh
    conn.close()

    conn2, is_fresh2 = _open_index(idx)
    assert is_fresh2 is False  # pre-existing, opened normally, no rebuild
    conn2.close()


def test_corrupt_index_self_heals_instead_of_crashing(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"

    # Build a healthy index, then corrupt it (kill -9 mid-write / bad block).
    _open_index(idx)[0].close()
    idx.write_bytes(b"CORRUPT-NOT-A-DUCKDB-FILE" * 500)

    # Boot re-open must NOT raise: the derived index is discarded and rebuilt.
    conn, is_fresh = _open_index(idx)
    try:
        # is_fresh=True signals the caller's cold-start ingest to re-materialize
        # from parquet (the rebuild).
        assert is_fresh is True
        # And it is a usable, empty, correctly-schema'd index.
        row = conn.execute("SELECT count(*) FROM runs_materialized").fetchone()
        assert row is not None
        assert row[0] == 0
    finally:
        conn.close()

    # The corrupt bytes are gone — the file is a valid DuckDB db again.
    conn3, _ = _open_index(idx)
    conn3.close()
