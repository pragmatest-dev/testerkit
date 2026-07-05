"""Runs daemon boot self-heal + derived-index versioning (#47, widened #53 P1):
neither a corrupt on-disk index file NOR an interrupted (crash-mid-build) one
may be a fatal poison-pill or silently serve an incomplete build.

Since #53 P1, the index is versioned by CONTENT-ADDRESSING THE FILENAME
(``_index.<fp>.duckdb``, ``fp`` = a widened fingerprint of the projection DDL
+ the adapter registry + the schema whitelist) rather than by comparing an
in-file stamp on open. ``_open_index`` guards two things:
  * an UNREADABLE file (kill -9 mid-write, bad block, DuckDB format bump) →
    discarded + rebuilt rather than crash-loop the daemon (#47, unchanged);
  * an INTERRUPTED BUILD (readable, correctly-named, but the build-complete
    marker in ``_index_meta`` was never written) → discarded + rebuilt rather
    than silently serve a partial index.
A file's SHAPE can no longer go stale in place: since the filename already
encodes this code's exact fingerprint, any file at that path is shaped like
this code's projection by construction — a shape mismatch is now impossible,
not just detected. Both self-heal paths funnel into a discard → rebuild
(``is_fresh=True``, cold-start ingest repopulates from parquet). Rationale
lives in ``_open_index``'s docstring.

``_open_index`` opens a DuckDB file only (no Flight server, no threads), so
``tmp_path`` is safe here — this is not a daemon-spawning test.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from litmus.data import _runs_duckdb_daemon as daemon
from litmus.data import schema_dispatch, schema_versions
from litmus.data._runs_duckdb_daemon import (
    _current_provenance,
    _open_index,
    _projection_fingerprint,
    _read_index_meta,
)
from litmus.data.schema_versions import SchemaStore


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


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


# ── Derived-index versioning (#47, widened #53 P1) ───────────────────


def test_fresh_build_stamps_index_meta(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        # A fresh build records full provenance and marks itself complete.
        litmus_version, schema_version, fingerprint = _current_provenance()
        meta = _read_index_meta(conn)
        assert meta["litmus_version"] == litmus_version
        assert meta["schema_version"] == schema_version
        assert meta["projection_fingerprint"] == fingerprint
        assert "built_at" in meta
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


def test_stale_in_file_stamp_no_longer_triggers_rebuild(tmp_path: Path) -> None:
    """Old (#47) model: an in-file ``(schema_version, fingerprint)`` mismatch
    triggered a rebuild. New (#53 P1) model: the FILENAME is the gate — the
    in-file stamp is provenance only and is never compared on open. A file's
    shape can only ever be this code's shape (it is named after this code's
    fingerprint), so tampering with the stored provenance strings must NOT
    force a rebuild as long as the build-complete marker is present.
    """
    idx = tmp_path / "_index.duckdb"
    _open_index(idx)[0].close()

    conn = duckdb.connect(str(idx))
    conn.execute(
        "UPDATE _index_meta SET value = 'stale-fingerprint' WHERE key = 'projection_fingerprint'"
    )
    conn.execute("UPDATE _index_meta SET value = '0.0-ancient' WHERE key = 'schema_version'")
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('KEPT-DESPITE-STALE-STAMP')")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is False  # built_at marker present → no rebuild
        assert _count(conn, "runs_materialized") == 1  # row preserved, not discarded
    finally:
        conn.close()


def test_build_incomplete_marker_absent_triggers_rebuild(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open_index(idx)[0].close()

    # Simulate a crash mid-build: the build-complete marker never got
    # written, but a data row (from before the crash) did.
    conn = duckdb.connect(str(idx))
    conn.execute("DELETE FROM _index_meta WHERE key = 'built_at'")
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('CRASH-MID-BUILD')")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True  # marker absent → treated as interrupted, rebuilt
        assert _count(conn, "runs_materialized") == 0
        assert "built_at" in _read_index_meta(conn)  # rebuild re-stamps a fresh marker
    finally:
        conn.close()


def test_missing_index_meta_entirely_rebuilds(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open_index(idx)[0].close()

    # A pre-#47 index has no _index_meta at all (so no marker either).
    conn = duckdb.connect(str(idx))
    conn.execute("DROP TABLE _index_meta")
    conn.execute("INSERT INTO runs_materialized (run_id) VALUES ('PRE-47')")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        assert _count(conn, "runs_materialized") == 0
        assert "built_at" in _read_index_meta(conn)
    finally:
        conn.close()


def test_two_fingerprints_coexist_without_clobbering(tmp_path: Path, monkeypatch) -> None:
    """The core #53 P1 invariant: two distinct fingerprints get two distinct,
    independently-owned index files in the same data dir; opening one never
    touches the other."""
    fp1 = _projection_fingerprint()
    idx1 = tmp_path / daemon._index_file_name(fp1)
    conn1, is_fresh1 = _open_index(idx1)
    assert is_fresh1 is True
    conn1.execute("INSERT INTO runs_materialized (run_id) VALUES ('FP1-ROW')")
    conn1.close()

    # A different projection (simulated the same way the DDL-fingerprint test
    # below does it) → a different fingerprint → a different filename.
    patched = (*daemon._MEASUREMENTS_PERSISTED_COLUMNS, ("_fp_coexist_probe", "VARCHAR"))
    monkeypatch.setattr(daemon, "_MEASUREMENTS_PERSISTED_COLUMNS", patched)
    fp2 = _projection_fingerprint()
    assert fp2 != fp1
    idx2 = tmp_path / daemon._index_file_name(fp2)
    assert idx2 != idx1

    conn2, is_fresh2 = _open_index(idx2)
    try:
        assert is_fresh2 is True  # fp2's own file, never seeded from fp1's
        assert _count(conn2, "runs_materialized") == 0
    finally:
        conn2.close()

    # fp1's file is untouched by fp2's open: still there, still has its row.
    assert idx1.exists()
    conn1b, is_fresh1b = _open_index(idx1)
    try:
        assert is_fresh1b is False
        assert _count(conn1b, "runs_materialized") == 1
    finally:
        conn1b.close()

    assert {p.name for p in tmp_path.glob("_index.*.duckdb")} == {idx1.name, idx2.name}


def test_epochs_ledger_gets_a_row_on_open(tmp_path: Path) -> None:
    fp = "c" * 64
    daemon._stamp_epochs_ledger(tmp_path, fp, "0.3.1")

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert fp[:12] in ledger
    entry = ledger[fp[:12]]
    assert entry["seen_by"] == ["0.3.1"]
    assert "last_seen" in entry


def test_epochs_ledger_upserts_without_losing_other_entries(tmp_path: Path) -> None:
    fp_a, fp_b = "a" * 64, "b" * 64
    daemon._stamp_epochs_ledger(tmp_path, fp_a, "0.3.0")
    daemon._stamp_epochs_ledger(tmp_path, fp_b, "0.3.1")

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert set(ledger) == {fp_a[:12], fp_b[:12]}
    assert ledger[fp_a[:12]]["seen_by"] == ["0.3.0"]
    assert ledger[fp_b[:12]]["seen_by"] == ["0.3.1"]


def test_epochs_ledger_accumulates_seen_by_as_a_set(tmp_path: Path) -> None:
    """SEEN BY (§7) is a SET of every version that opened this epoch —
    accumulate across opens (dedup, sorted), never overwrite."""
    fp = "e" * 64
    daemon._stamp_epochs_ledger(tmp_path, fp, "0.3.0")
    daemon._stamp_epochs_ledger(tmp_path, fp, "0.2.4")
    daemon._stamp_epochs_ledger(tmp_path, fp, "0.3.0")  # re-open by same version: no dup

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    entry = ledger[fp[:12]]
    assert entry["seen_by"] == ["0.2.4", "0.3.0"]


def test_epochs_ledger_tolerates_legacy_single_version_shape(tmp_path: Path) -> None:
    """A pre-P5 ledger entry (``{litmus_version, last_seen}``, no ``seen_by``
    set yet) must not crash a later stamp — it folds into a 1-element
    ``seen_by`` before the new version is appended."""
    ledger_path = tmp_path / "_epochs.json"
    fp = "f" * 64
    ledger_path.write_text(
        json.dumps({fp[:12]: {"litmus_version": "0.3.0", "last_seen": "2026-01-01T00:00:00+00:00"}})
    )

    daemon._stamp_epochs_ledger(tmp_path, fp, "0.3.1")

    ledger = json.loads(ledger_path.read_text())
    assert ledger[fp[:12]]["seen_by"] == ["0.3.0", "0.3.1"]


def test_epochs_ledger_write_failure_is_swallowed(tmp_path: Path) -> None:
    # A ledger write failure (here: the directory doesn't exist) must never
    # raise — it is best-effort bookkeeping, not load-bearing.
    missing_dir = tmp_path / "does-not-exist"
    daemon._stamp_epochs_ledger(missing_dir, "d" * 64, "0.3.1")  # must not raise


def test_read_epochs_ledger_normalizes_legacy_shape(tmp_path: Path) -> None:
    ledger_path = tmp_path / "_epochs.json"
    ledger_path.write_text(
        json.dumps(
            {"abc123def456": {"litmus_version": "0.3.0", "last_seen": "2026-01-01T00:00:00+00:00"}}
        )
    )

    normalized = daemon._read_epochs_ledger(tmp_path)
    assert normalized["abc123def456"]["seen_by"] == ["0.3.0"]
    assert normalized["abc123def456"]["last_seen"] == "2026-01-01T00:00:00+00:00"


def test_read_epochs_ledger_reads_current_shape(tmp_path: Path) -> None:
    fp = "1" * 64
    daemon._stamp_epochs_ledger(tmp_path, fp, "0.3.1")
    daemon._stamp_epochs_ledger(tmp_path, fp, "0.2.4")

    normalized = daemon._read_epochs_ledger(tmp_path)
    assert normalized[fp[:12]]["seen_by"] == ["0.2.4", "0.3.1"]


def test_read_epochs_ledger_missing_file_returns_empty(tmp_path: Path) -> None:
    assert daemon._read_epochs_ledger(tmp_path / "nope") == {}


def test_remove_epochs_ledger_entries(tmp_path: Path) -> None:
    fp_a, fp_b = "a" * 64, "b" * 64
    daemon._stamp_epochs_ledger(tmp_path, fp_a, "0.3.0")
    daemon._stamp_epochs_ledger(tmp_path, fp_b, "0.3.1")

    daemon._remove_epochs_ledger_entries(tmp_path, {fp_a[:12]})

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert set(ledger) == {fp_b[:12]}


def test_remove_epochs_ledger_entries_missing_ledger_is_noop(tmp_path: Path) -> None:
    # No _epochs.json exists yet — must not raise.
    daemon._remove_epochs_ledger_entries(tmp_path, {"whatever"})


def test_fingerprint_is_stable_across_calls() -> None:
    # Deterministic: same DDL + adapters + whitelist → same 64-char sha256, every call.
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


def test_fingerprint_changes_when_adapter_registered(monkeypatch) -> None:
    # #53 P1 widening: a registered adapter key folds into the hash even when
    # the projection DDL is untouched.
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.RUNS], "0.0-fp-probe", lambda rows: rows
    )
    after = _projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_whitelist_grows(monkeypatch) -> None:
    # #53 P1 widening: the schema whitelist folds into the hash too.
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        SchemaStore.RUNS,
        schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.RUNS] | {"0.0-fp-probe"},
    )
    after = _projection_fingerprint()
    assert after != before
