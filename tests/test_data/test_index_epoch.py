"""Unit tests for the store-agnostic derived-index epoch primitives (#64).

Extracted from ``_runs_duckdb_daemon.py`` into ``litmus.data._index_epoch`` as
the shared spine events/channels/files will reuse later (#53, #64). See
``docs/_internal/explorations/derived-index-versioning.md`` §3/§6.

Pure — a DuckDB ``:memory:`` connection or a ``tmp_path`` file directly; no
daemon spawn, no Flight server, no threads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from litmus.data import _index_epoch as index_epoch

# ── index_file_name ──────────────────────────────────────────────────


def test_index_file_name_uses_12_char_prefix() -> None:
    fp = "a" * 64
    assert index_epoch.index_file_name(fp) == f"_index.{'a' * 12}.duckdb"


def test_index_file_name_differs_on_differing_fingerprints() -> None:
    assert index_epoch.index_file_name("a" * 64) != index_epoch.index_file_name("b" * 64)


# ── stamp_index_meta / read_index_meta ───────────────────────────────


def test_stamp_and_read_index_meta_round_trip() -> None:
    conn = duckdb.connect(":memory:")
    try:
        index_epoch.stamp_index_meta(
            conn, litmus_version="1.2.3", schema_version="0.1", fingerprint="f" * 64
        )
        meta = index_epoch.read_index_meta(conn)
        assert meta["litmus_version"] == "1.2.3"
        assert meta["schema_version"] == "0.1"
        assert meta["projection_fingerprint"] == "f" * 64
        assert "built_at" in meta
    finally:
        conn.close()


def test_stamp_index_meta_writes_built_at_in_a_separate_last_statement() -> None:
    """The build-complete marker must be the LAST statement executed — a
    crash between the provenance insert and this one must leave it absent
    (see :func:`_index_epoch.open_index`'s build-incomplete self-heal)."""
    executed: list[str] = []

    class _Recorder:
        def __init__(self, real: duckdb.DuckDBPyConnection) -> None:
            self._real = real

        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            executed.append(sql)
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    real = duckdb.connect(":memory:")
    try:
        index_epoch.stamp_index_meta(
            _Recorder(real),  # type: ignore[arg-type]
            litmus_version="1.0",
            schema_version="0.1",
            fingerprint="a" * 64,
        )
        assert "built_at" in executed[-1]
        assert all("built_at" not in sql for sql in executed[:-1])
    finally:
        real.close()


def test_read_index_meta_missing_table_returns_empty() -> None:
    conn = duckdb.connect(":memory:")
    try:
        assert index_epoch.read_index_meta(conn) == {}
    finally:
        conn.close()


def test_stamp_index_meta_is_idempotent_upsert() -> None:
    conn = duckdb.connect(":memory:")
    try:
        index_epoch.stamp_index_meta(
            conn, litmus_version="1.0", schema_version="0.1", fingerprint="a" * 64
        )
        index_epoch.stamp_index_meta(
            conn, litmus_version="2.0", schema_version="0.2", fingerprint="b" * 64
        )
        meta = index_epoch.read_index_meta(conn)
        assert meta["litmus_version"] == "2.0"
        assert meta["schema_version"] == "0.2"
        assert meta["projection_fingerprint"] == "b" * 64
    finally:
        conn.close()


# ── epochs ledger ─────────────────────────────────────────────────────


def test_stamp_epochs_ledger_creates_entry(tmp_path: Path) -> None:
    fp = "c" * 64
    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.3.1")

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert ledger[fp[:12]]["seen_by"] == ["0.3.1"]
    assert "last_seen" in ledger[fp[:12]]


def test_stamp_epochs_ledger_upserts_without_losing_other_entries(tmp_path: Path) -> None:
    fp_a, fp_b = "a" * 64, "b" * 64
    index_epoch.stamp_epochs_ledger(tmp_path, fp_a, "0.3.0")
    index_epoch.stamp_epochs_ledger(tmp_path, fp_b, "0.3.1")

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert set(ledger) == {fp_a[:12], fp_b[:12]}
    assert ledger[fp_a[:12]]["seen_by"] == ["0.3.0"]
    assert ledger[fp_b[:12]]["seen_by"] == ["0.3.1"]


def test_stamp_epochs_ledger_accumulates_seen_by_as_a_sorted_set(tmp_path: Path) -> None:
    fp = "e" * 64
    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.3.0")
    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.2.4")
    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.3.0")  # re-open, same version: no dup

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert ledger[fp[:12]]["seen_by"] == ["0.2.4", "0.3.0"]


def test_stamp_epochs_ledger_tolerates_legacy_single_version_shape(tmp_path: Path) -> None:
    ledger_path = tmp_path / "_epochs.json"
    fp = "f" * 64
    ledger_path.write_text(
        json.dumps({fp[:12]: {"litmus_version": "0.3.0", "last_seen": "2026-01-01T00:00:00+00:00"}})
    )

    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.3.1")

    ledger = json.loads(ledger_path.read_text())
    assert ledger[fp[:12]]["seen_by"] == ["0.3.0", "0.3.1"]


def test_stamp_epochs_ledger_write_failure_is_swallowed(tmp_path: Path) -> None:
    # A ledger write failure (here: the directory doesn't exist) must never
    # raise — it is best-effort bookkeeping, not load-bearing.
    missing_dir = tmp_path / "does-not-exist"
    index_epoch.stamp_epochs_ledger(missing_dir, "d" * 64, "0.3.1")  # must not raise


def test_read_epochs_ledger_normalizes_legacy_shape(tmp_path: Path) -> None:
    ledger_path = tmp_path / "_epochs.json"
    ledger_path.write_text(
        json.dumps(
            {"abc123def456": {"litmus_version": "0.3.0", "last_seen": "2026-01-01T00:00:00+00:00"}}
        )
    )

    normalized = index_epoch.read_epochs_ledger(tmp_path)
    assert normalized["abc123def456"]["seen_by"] == ["0.3.0"]
    assert normalized["abc123def456"]["last_seen"] == "2026-01-01T00:00:00+00:00"


def test_read_epochs_ledger_reads_current_shape(tmp_path: Path) -> None:
    fp = "1" * 64
    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.3.1")
    index_epoch.stamp_epochs_ledger(tmp_path, fp, "0.2.4")

    normalized = index_epoch.read_epochs_ledger(tmp_path)
    assert normalized[fp[:12]]["seen_by"] == ["0.2.4", "0.3.1"]


def test_read_epochs_ledger_missing_file_returns_empty(tmp_path: Path) -> None:
    assert index_epoch.read_epochs_ledger(tmp_path / "nope") == {}


def test_remove_epochs_ledger_entries(tmp_path: Path) -> None:
    fp_a, fp_b = "a" * 64, "b" * 64
    index_epoch.stamp_epochs_ledger(tmp_path, fp_a, "0.3.0")
    index_epoch.stamp_epochs_ledger(tmp_path, fp_b, "0.3.1")

    index_epoch.remove_epochs_ledger_entries(tmp_path, {fp_a[:12]})

    ledger = json.loads((tmp_path / "_epochs.json").read_text())
    assert set(ledger) == {fp_b[:12]}


def test_remove_epochs_ledger_entries_missing_ledger_is_noop(tmp_path: Path) -> None:
    # No _epochs.json exists yet — must not raise.
    index_epoch.remove_epochs_ledger_entries(tmp_path, {"whatever"})


# ── discard_index / index_file_is_the_cause / reset_index ────────────


def test_discard_index_removes_file_and_wal(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    idx.write_bytes(b"x")
    wal = tmp_path / "_index.duckdb.wal"
    wal.write_bytes(b"y")

    index_epoch.discard_index(idx)

    assert not idx.exists()
    assert not wal.exists()


def test_discard_index_missing_file_is_noop(tmp_path: Path) -> None:
    index_epoch.discard_index(tmp_path / "nope.duckdb")  # must not raise


def test_index_file_is_the_cause_true_for_a_healthy_directory(tmp_path: Path) -> None:
    assert index_epoch.index_file_is_the_cause(tmp_path) is True


def _fake_ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS probe (marker VARCHAR)")


def test_reset_index_discards_and_reopens_empty(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn = duckdb.connect(str(idx))
    _fake_ensure_schema(conn)
    conn.execute("INSERT INTO probe (marker) VALUES ('GONE')")
    conn.close()

    conn2 = index_epoch.reset_index(idx, ensure_schema=_fake_ensure_schema)
    try:
        row = conn2.execute("SELECT count(*) FROM probe").fetchone()
        assert row is not None
        assert row[0] == 0
    finally:
        conn2.close()


# ── open_index (injected callables) ──────────────────────────────────


def _fake_stamp_meta(conn: duckdb.DuckDBPyConnection) -> None:
    index_epoch.stamp_index_meta(
        conn, litmus_version="9.9.9", schema_version="9.9", fingerprint="9" * 64
    )


def _open(index_path: Path) -> tuple[duckdb.DuckDBPyConnection, bool]:
    return index_epoch.open_index(
        index_path,
        ensure_schema=_fake_ensure_schema,
        stamp_meta=_fake_stamp_meta,
        index_file_is_the_cause=lambda _dir: True,
    )


def test_open_index_fresh_file_is_marked_fresh_and_stamped(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn, is_fresh = _open(idx)
    try:
        assert is_fresh is True
        assert "built_at" in index_epoch.read_index_meta(conn)
    finally:
        conn.close()


def test_open_index_existing_complete_build_opens_normally_keeping_rows(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn, _ = _open(idx)
    conn.execute("INSERT INTO probe (marker) VALUES ('KEEP')")
    conn.close()

    conn2, is_fresh2 = _open(idx)
    try:
        assert is_fresh2 is False
        row = conn2.execute("SELECT count(*) FROM probe").fetchone()
        assert row is not None
        assert row[0] == 1
    finally:
        conn2.close()


def test_open_index_build_incomplete_marker_absent_triggers_rebuild(tmp_path: Path) -> None:
    """A file with meta but NO ``built_at`` marker (crash mid-build) must be
    discarded and rebuilt from scratch, never silently served."""
    idx = tmp_path / "_index.duckdb"
    conn, _ = _open(idx)
    conn.execute("INSERT INTO probe (marker) VALUES ('CRASH-MID-BUILD')")
    conn.execute("DELETE FROM _index_meta WHERE key = 'built_at'")
    conn.close()

    conn2, is_fresh2 = _open(idx)
    try:
        assert is_fresh2 is True  # marker absent → treated as interrupted, rebuilt
        row = conn2.execute("SELECT count(*) FROM probe").fetchone()
        assert row is not None
        assert row[0] == 0  # old row discarded with the file
        assert "built_at" in index_epoch.read_index_meta(conn2)  # rebuild re-stamps
    finally:
        conn2.close()


def test_open_index_missing_index_meta_entirely_rebuilds(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    conn, _ = _open(idx)
    conn.execute("INSERT INTO probe (marker) VALUES ('PRE-EPOCH')")
    conn.execute("DROP TABLE _index_meta")
    conn.close()

    conn2, is_fresh2 = _open(idx)
    try:
        assert is_fresh2 is True
        row = conn2.execute("SELECT count(*) FROM probe").fetchone()
        assert row is not None
        assert row[0] == 0
        assert "built_at" in index_epoch.read_index_meta(conn2)
    finally:
        conn2.close()


def test_open_index_unreadable_file_self_heals_when_index_is_the_cause(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open(idx)[0].close()
    idx.write_bytes(b"CORRUPT-NOT-A-DUCKDB-FILE" * 500)

    conn, is_fresh = _open(idx)
    try:
        assert is_fresh is True
        row = conn.execute("SELECT count(*) FROM probe").fetchone()
        assert row is not None
        assert row[0] == 0
    finally:
        conn.close()


def test_open_index_unreadable_file_reraises_when_not_the_cause(tmp_path: Path) -> None:
    idx = tmp_path / "_index.duckdb"
    _open(idx)[0].close()
    idx.write_bytes(b"CORRUPT-NOT-A-DUCKDB-FILE" * 500)

    with pytest.raises(duckdb.Error):
        index_epoch.open_index(
            idx,
            ensure_schema=_fake_ensure_schema,
            stamp_meta=_fake_stamp_meta,
            index_file_is_the_cause=lambda _dir: False,  # environmental fault, not the file
        )


def test_open_index_fresh_file_fault_always_reraises_never_self_heals(tmp_path: Path) -> None:
    """A brand-new file failing isn't corruption — it's an env/DuckDB fault
    (e.g. disk full), so it must re-raise even if ``index_file_is_the_cause``
    (wrongly) says the probe succeeds."""
    idx = tmp_path / "_index.duckdb"

    def _bad_ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
        raise duckdb.IOException("simulated disk full")

    with pytest.raises(duckdb.Error):
        index_epoch.open_index(
            idx,
            ensure_schema=_bad_ensure_schema,
            stamp_meta=_fake_stamp_meta,
            index_file_is_the_cause=lambda _dir: True,
        )
