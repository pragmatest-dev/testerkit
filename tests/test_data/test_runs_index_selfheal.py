"""Runs daemon boot self-heal + derived-index versioning (#47): neither a
corrupt on-disk ``_index.duckdb`` NOR a readable-but-wrong-shape one may be a
fatal poison-pill or silently serve the old projection.

The index is a DERIVED cache (rebuildable from parquet) versioned by two
things ``_open_index`` guards:
  * an UNREADABLE file (kill -9 mid-write, bad block, DuckDB format bump) →
    discarded + rebuilt rather than crash-loop the daemon;
  * a STALE PROJECTION SHAPE (readable, but its stored ``(schema_version,
    projection_fingerprint)`` stamp differs from the current DDL) → discarded +
    rebuilt rather than serve the old shape / error when the new views
    reference columns the stale tables lack.
Both funnel into a discard → rebuild (``is_fresh=True``, cold-start ingest
repopulates from parquet). Rationale lives in ``_open_index``'s docstring.

``_open_index`` opens a DuckDB file only (no Flight server, no threads), so
``tmp_path`` is safe here — this is not a daemon-spawning test.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from litmus.data import _runs_duckdb_daemon as daemon
from litmus.data._runs_duckdb_daemon import (
    _current_index_stamp,
    _open_index,
    _projection_fingerprint,
    _read_index_meta,
)


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


# ── Derived-index versioning (#47) ──────────────────────────────────


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_fresh_build_stamps_index_meta(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        # A fresh build records the current (schema_version, projection_fingerprint).
        assert _read_index_meta(conn) == _current_index_stamp()
    finally:
        conn.close()


def test_matching_stamp_opens_normally_keeping_data(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn, _ = _open_index(idx)
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('KEEP')")
    conn.close()

    # Reopen with an UNCHANGED shape → no rebuild, existing rows preserved.
    conn2, is_fresh2 = _open_index(idx)
    try:
        assert is_fresh2 is False
        assert _count(conn2, "runs_materialized") == 1
    finally:
        conn2.close()


def test_stale_fingerprint_discards_and_rebuilds(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open_index(idx)[0].close()

    # Simulate a projection-shape change landing under a still-valid index:
    # overwrite the stored fingerprint with a wrong value and seed a marker row.
    conn = duckdb.connect(str(idx))
    conn.execute(
        "UPDATE _index_meta SET value = 'stale-fingerprint' WHERE key = 'projection_fingerprint'"
    )
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('STALE-MARKER')")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        # Rebuilt: is_fresh signals the cold-start ingest; the marker is gone
        # (whole index discarded), the stamp is current again, and the CURRENT
        # snowflake shape is present (vectors_materialized is a 0.3.1 table).
        assert is_fresh is True
        assert _count(conn, "runs_materialized") == 0
        assert _count(conn, "vectors_materialized") == 0
        assert _read_index_meta(conn) == _current_index_stamp()
    finally:
        conn.close()


def test_stale_schema_version_discards_and_rebuilds(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open_index(idx)[0].close()

    conn = duckdb.connect(str(idx))
    conn.execute("UPDATE _index_meta SET value = '0.0-ancient' WHERE key = 'schema_version'")
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('OLD-SV')")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True  # at-rest-version axis differs → rebuild
        assert _count(conn, "runs_materialized") == 0
        assert _read_index_meta(conn) == _current_index_stamp()
    finally:
        conn.close()


def test_missing_index_meta_rebuilds(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open_index(idx)[0].close()

    # A pre-#47 index has no _index_meta at all → counts as a mismatch.
    conn = duckdb.connect(str(idx))
    conn.execute("DROP TABLE _index_meta")
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('PRE-47')")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        assert _count(conn, "runs_materialized") == 0
        assert _read_index_meta(conn) == _current_index_stamp()
    finally:
        conn.close()


def test_fingerprint_is_stable_across_calls() -> None:
    # Deterministic: same DDL → same 64-char sha256, every call.
    fp1 = _projection_fingerprint()
    fp2 = _projection_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_changes_when_ddl_changes(monkeypatch) -> None:
    # Adding a column to a persisted-columns tuple changes the ALTER/CREATE the
    # daemon runs → a different fingerprint (the auto-detect the MVP relies on).
    before = _projection_fingerprint()
    patched = (*daemon._MEASUREMENTS_PERSISTED_COLUMNS, ("_fp_probe_col", "VARCHAR"))
    monkeypatch.setattr(daemon, "_MEASUREMENTS_PERSISTED_COLUMNS", patched)
    after = _projection_fingerprint()
    assert after != before
