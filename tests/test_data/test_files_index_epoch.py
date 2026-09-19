"""Files catalog derived-index versioning parity with runs/events/channels (#64).

Brings the files catalog onto the same content-addressed-epoch scheme
runs/events/channels already use (#53 P1, #64), reusing the shared,
store-agnostic ``testerkit.data._index_epoch`` primitives. See
``docs/_internal/explorations/derived-index-versioning.md`` §3/§6 and
mirrors ``test_events_index_epoch.py`` / ``test_channels_index_epoch.py``,
scoped to the files catalog's actual API — the catalog is opened via
``testerkit.data.files.catalog._open_index`` (the thin store-level wrapper
the daemon itself calls) and populated via :func:`scan_sidecars`, no daemon
process, no Flight, no threads.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

from testerkit.data import _index_epoch as index_epoch
from testerkit.data import schema_dispatch, schema_versions
from testerkit.data.files import catalog as files_catalog_module
from testerkit.data.files.catalog import _open_index, scan_sidecars
from testerkit.data.files.store import FileStore
from testerkit.data.schema_versions import SchemaStore


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


# ── _projection_fingerprint determinism + widening ───────────────────


def test_fingerprint_is_stable_across_calls() -> None:
    fp1 = files_catalog_module._projection_fingerprint()
    fp2 = files_catalog_module._projection_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_changes_when_catalog_ddl_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    before = files_catalog_module._projection_fingerprint()
    patched_ddl = files_catalog_module.CATALOG_DDL.replace(
        "attributes VARCHAR", "attributes VARCHAR, _fp_probe_col VARCHAR"
    )
    assert patched_ddl != files_catalog_module.CATALOG_DDL
    monkeypatch.setattr(files_catalog_module, "CATALOG_DDL", patched_ddl)
    after = files_catalog_module._projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_adapter_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    before = files_catalog_module._projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.FILES], "0.0-fp-probe", lambda meta: meta
    )
    after = files_catalog_module._projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_whitelist_grows(monkeypatch: pytest.MonkeyPatch) -> None:
    before = files_catalog_module._projection_fingerprint()
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        SchemaStore.FILES,
        schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.FILES] | {"0.0-fp-probe"},
    )
    after = files_catalog_module._projection_fingerprint()
    assert after != before


def test_fingerprint_unaffected_by_unrelated_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registering an adapter for a DIFFERENT store (e.g. runs) must not
    change the files fingerprint."""
    before = files_catalog_module._projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.RUNS], "0.0-fp-probe", lambda rows: rows
    )
    after = files_catalog_module._projection_fingerprint()
    assert after == before


# ── content-addressed filename parity with runs/events/channels ────────


def test_index_file_name_matches_current_fingerprint(tmp_path: Path) -> None:
    fp = files_catalog_module._projection_fingerprint()
    idx = tmp_path / index_epoch.index_file_name(fp)
    assert idx.name == f"_index.{fp[:12]}.duckdb"


def test_open_creates_fingerprinted_file_not_fixed_name(tmp_path: Path) -> None:
    """The daemon no longer opens a fixed ``_index.duckdb`` — it opens the
    content-addressed ``_index.<fp>.duckdb`` (runs/events/channels parity)."""
    store = FileStore(_data_dir=tmp_path)
    store.write("a.bin", b"aaa", session_id=uuid4().hex)

    files_dir = tmp_path / "files"
    fp = files_catalog_module._projection_fingerprint()
    index_path = files_dir / index_epoch.index_file_name(fp)
    conn, _ = _open_index(index_path)
    scan_sidecars(conn, files_dir)
    conn.close()

    assert not (files_dir / "_index.duckdb").exists()
    assert index_path.exists()


# ── epochs ledger ───────────────────────────────────────────────────


def test_open_stamps_epochs_ledger(tmp_path: Path) -> None:
    from testerkit.data._daemon_lifecycle import _installed_version

    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)
    fp = files_catalog_module._projection_fingerprint()
    index_path = files_dir / index_epoch.index_file_name(fp)
    conn, _ = _open_index(index_path)
    index_epoch.stamp_epochs_ledger(files_dir, fp, _installed_version())
    conn.close()

    ledger = json.loads((files_dir / "_epochs.json").read_text())
    assert fp[:12] in ledger
    assert "last_seen" in ledger[fp[:12]]


# ── fork-on-schema-change (the scenario that crashed a fixed filename) ──


def test_fork_on_schema_change_coexists_and_rebuilds_from_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the crash a single fixed ``_index.duckdb`` produced: a
    schema/read-path change used to force either an in-place mutation of the
    one shared catalog file or a hard crash on an incompatible reopen. With
    the epoch mechanism, a NEW fingerprint forks a NEW file — the old one is
    left completely untouched — and the new file rebuilds cleanly from the
    durable sidecars, never raising.
    """
    store = FileStore(_data_dir=tmp_path)
    sid = uuid4().hex
    store.write("a.bin", b"aaa", session_id=sid)
    store.write("b.bin", b"bbb", session_id=sid)

    files_dir = tmp_path / "files"

    # Build the catalog at the CURRENT fingerprint.
    fp1 = files_catalog_module._projection_fingerprint()
    idx1 = files_dir / index_epoch.index_file_name(fp1)
    conn1, is_fresh1 = _open_index(idx1)
    assert is_fresh1 is True
    assert scan_sidecars(conn1, files_dir) == 2
    assert _count(conn1, "file_catalog") == 2
    conn1.close()
    assert idx1.exists()

    # Simulate a schema/read-path change: a different fingerprint.
    fake_fp = "e" * 64
    monkeypatch.setattr(files_catalog_module, "_projection_fingerprint", lambda: fake_fp)

    idx2 = files_dir / index_epoch.index_file_name(fake_fp)
    conn2, is_fresh2 = _open_index(idx2)  # must NOT raise
    try:
        assert is_fresh2 is True
        n_scanned = scan_sidecars(conn2, files_dir)
        assert n_scanned == 2, "the new epoch rebuilds from the durable sidecars"
        assert _count(conn2, "file_catalog") == 2
    finally:
        conn2.close()

    assert idx2.exists()
    assert idx2 != idx1
    assert idx1.exists(), "the original fingerprinted file must be left untouched"
    assert {p.name for p in files_dir.glob("_index.*.duckdb")} == {idx1.name, idx2.name}

    # The original epoch's data is intact — reopening it (fingerprint
    # restored) serves the same rows, unaffected by the new epoch's birth.
    monkeypatch.undo()
    conn1b, is_fresh1b = _open_index(idx1)
    try:
        assert is_fresh1b is False
        assert _count(conn1b, "file_catalog") == 2
    finally:
        conn1b.close()
