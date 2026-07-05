"""Events derived-index versioning parity with runs (#64).

Brings the events DuckDB daemon's index to the same content-addressed-epoch
scheme the runs daemon already has (#53 P1), reusing the shared, store-
agnostic ``litmus.data._index_epoch`` primitives. See
``docs/_internal/explorations/derived-index-versioning.md`` §3/§6 and
``versioning-resiliency-backlog.md`` §F.

Mirrors ``test_runs_index_selfheal.py`` scoped to what's relevant for
events: no SATB cascade-delete freeze (events' ``_ingest_ipc_files`` never
prunes gone files, so there is no sweep race to guard), and the fingerprint
folds in BOTH of events' schema coordinates (``EVENTS_ENVELOPE`` +
``EVENT_CATALOG``), not just one.

``_open_index`` opens a DuckDB file only (no Flight server, no threads), so
``tmp_path`` is safe here — this is not a daemon-spawning test.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from litmus.data import _duckdb_daemon as daemon
from litmus.data import schema_dispatch, schema_versions
from litmus.data._duckdb_daemon import (
    _current_provenance,
    _open_index,
    _projection_fingerprint,
)
from litmus.data._index_epoch import index_file_name, read_index_meta
from litmus.data.schema_versions import SchemaStore


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


# ── _projection_fingerprint determinism + widening ───────────────────


def test_fingerprint_is_stable_across_calls() -> None:
    fp1 = _projection_fingerprint()
    fp2 = _projection_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_changes_when_ddl_changes(monkeypatch) -> None:
    before = _projection_fingerprint()
    patched = (*daemon._EVENTS_COLUMNS, ("_fp_probe_col", "VARCHAR"))
    monkeypatch.setattr(daemon, "_EVENTS_COLUMNS", patched)
    after = _projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_envelope_adapter_registered(monkeypatch) -> None:
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.EVENTS_ENVELOPE],
        "0.0-fp-probe",
        lambda rows: rows,
    )
    after = _projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_catalog_adapter_registered(monkeypatch) -> None:
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.EVENT_CATALOG],
        "0.0-fp-probe",
        lambda rows: rows,
    )
    after = _projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_envelope_whitelist_grows(monkeypatch) -> None:
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        SchemaStore.EVENTS_ENVELOPE,
        schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.EVENTS_ENVELOPE] | {"0.0-fp-probe"},
    )
    after = _projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_catalog_whitelist_grows(monkeypatch) -> None:
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        SchemaStore.EVENT_CATALOG,
        schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.EVENT_CATALOG] | {"0.0-fp-probe"},
    )
    after = _projection_fingerprint()
    assert after != before


def test_fingerprint_unaffected_by_unrelated_store(monkeypatch) -> None:
    """Registering an adapter for a DIFFERENT store (e.g. runs) must not
    change the events fingerprint — the widening is scoped to events' own
    two coordinates only."""
    before = _projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.RUNS], "0.0-fp-probe", lambda rows: rows
    )
    after = _projection_fingerprint()
    assert after == before


# ── content-addressed filename ────────────────────────────────────────


def test_index_file_name_matches_current_fingerprint(tmp_path: Path) -> None:
    fp = _projection_fingerprint()
    idx = tmp_path / index_file_name(fp)
    assert idx.name == f"_index.{fp[:12]}.duckdb"


# ── open_index self-heal + provenance (mirrors test_runs_index_selfheal) ──


def test_fresh_then_existing(tmp_path: Path) -> None:
    idx = tmp_path / index_file_name(_projection_fingerprint())

    conn, is_fresh = _open_index(idx)
    assert is_fresh is True
    conn.close()

    conn2, is_fresh2 = _open_index(idx)
    assert is_fresh2 is False
    conn2.close()


def test_corrupt_index_self_heals_instead_of_crashing(tmp_path: Path) -> None:
    idx = tmp_path / index_file_name(_projection_fingerprint())

    _open_index(idx)[0].close()
    idx.write_bytes(b"CORRUPT-NOT-A-DUCKDB-FILE" * 500)

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        assert _count(conn, "events") == 0
    finally:
        conn.close()

    conn3, _ = _open_index(idx)
    conn3.close()


def test_fresh_build_stamps_index_meta(tmp_path: Path) -> None:
    idx = tmp_path / index_file_name(_projection_fingerprint())
    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        litmus_version, schema_version, fingerprint = _current_provenance()
        meta = read_index_meta(conn)
        assert meta["litmus_version"] == litmus_version
        assert meta["schema_version"] == schema_version
        assert "envelope=" in meta["schema_version"]
        assert "catalog=" in meta["schema_version"]
        assert meta["projection_fingerprint"] == fingerprint
        assert "built_at" in meta
    finally:
        conn.close()


def test_matching_stamp_opens_normally_keeping_data(tmp_path: Path) -> None:
    idx = tmp_path / index_file_name(_projection_fingerprint())
    conn, _ = _open_index(idx)
    conn.execute("INSERT INTO events (id, event_type, occurred_at) VALUES ('E-KEEP', 'x', now())")
    conn.close()

    conn2, is_fresh2 = _open_index(idx)
    try:
        assert is_fresh2 is False
        assert _count(conn2, "events") == 1
    finally:
        conn2.close()


def test_build_incomplete_marker_absent_triggers_rebuild(tmp_path: Path) -> None:
    idx = tmp_path / index_file_name(_projection_fingerprint())
    _open_index(idx)[0].close()

    conn = duckdb.connect(str(idx))
    conn.execute("DELETE FROM _index_meta WHERE key = 'built_at'")
    conn.execute("INSERT INTO events (id, event_type, occurred_at) VALUES ('E-CRASH', 'x', now())")
    conn.close()

    conn, is_fresh = _open_index(idx)
    try:
        assert is_fresh is True
        assert _count(conn, "events") == 0
    finally:
        conn.close()


def test_two_fingerprints_coexist_without_clobbering(tmp_path: Path, monkeypatch) -> None:
    fp1 = _projection_fingerprint()
    idx1 = tmp_path / index_file_name(fp1)
    conn1, is_fresh1 = _open_index(idx1)
    assert is_fresh1 is True
    conn1.execute("INSERT INTO events (id, event_type, occurred_at) VALUES ('FP1', 'x', now())")
    conn1.close()

    patched = (*daemon._EVENTS_COLUMNS, ("_fp_coexist_probe", "VARCHAR"))
    monkeypatch.setattr(daemon, "_EVENTS_COLUMNS", patched)
    fp2 = _projection_fingerprint()
    assert fp2 != fp1
    idx2 = tmp_path / index_file_name(fp2)
    assert idx2 != idx1

    conn2, is_fresh2 = _open_index(idx2)
    try:
        assert is_fresh2 is True
        assert _count(conn2, "events") == 0
    finally:
        conn2.close()

    assert idx1.exists()
    conn1b, is_fresh1b = _open_index(idx1)
    try:
        assert is_fresh1b is False
        assert _count(conn1b, "events") == 1
    finally:
        conn1b.close()

    assert {p.name for p in tmp_path.glob("_index.*.duckdb")} == {idx1.name, idx2.name}
