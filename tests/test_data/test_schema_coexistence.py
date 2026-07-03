"""Layer 2 — mixed-version coexistence, adapted at read, through the REAL files
catalog scan.

A legacy (adapter-backed) ``0.0`` sidecar and a current ``1.0`` sidecar are both
written to a real files dir; the real ``scan_sidecars`` ingests BOTH into one
``file_catalog``, and the ``0.0`` one arrives RESHAPED by its adapter. The read
path is production code (``ensure_schema`` + ``scan_sidecars`` + the catalog
table) — only the version string and the transform are synthetic. This is the
"mixed-version coexistence, adapted at read is the scalable truth" property (§0)
exercised end-to-end on one store.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from litmus.data import schema_dispatch, schema_versions
from litmus.data.files.catalog import ensure_schema, scan_sidecars
from litmus.data.files.models import FileArtifactMetadata
from litmus.data.schema_versions import SchemaStore


def _register_legacy(monkeypatch, store: SchemaStore, version: str, adapter) -> None:
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        store,
        schema_versions.KNOWN_SCHEMA_VERSIONS[store] | {version},
    )
    monkeypatch.setitem(schema_dispatch._ADAPTERS[store], version, adapter)


def _write_sidecar(files_dir: Path, session: str, name: str, version: str | None) -> None:
    day = files_dir / "2026-07-02" / session
    day.mkdir(parents=True, exist_ok=True)
    (day / name).write_text("blob")  # the artifact scan_sidecars requires alongside
    meta = FileArtifactMetadata(mime="text/plain", extension="txt", size_bytes=4)
    raw = meta.model_dump()
    if version is None:
        raw.pop("schema_version")  # simulate an unstamped sidecar
    else:
        raw["schema_version"] = version
    (day / f"{name}.meta.json").write_text(json.dumps(raw))


def test_legacy_and_current_sidecars_coexist_adapted_at_read(tmp_path, monkeypatch) -> None:
    conn = duckdb.connect()
    ensure_schema(conn)
    files_dir = tmp_path / "files"

    # A real 0.0 -> current reshape: mark the model so we can prove it ran.
    def reshape(meta: FileArtifactMetadata) -> FileArtifactMetadata:
        return meta.model_copy(update={"attributes": {**meta.attributes, "adapted_from": "0.0"}})

    _register_legacy(monkeypatch, SchemaStore.FILES, "0.0", reshape)

    current = schema_versions.CURRENT_SCHEMA_VERSION[SchemaStore.FILES]
    _write_sidecar(files_dir, "S-OLD", "old.txt", "0.0")
    _write_sidecar(files_dir, "S-NEW", "new.txt", current)

    assert scan_sidecars(conn, files_dir) == 2  # BOTH versions coexist in one catalog

    rows = dict(conn.execute("SELECT name, attributes FROM file_catalog").fetchall())
    assert json.loads(rows["old.txt"]).get("adapted_from") == "0.0"  # 0.0 reshaped at ingest
    assert json.loads(rows["new.txt"]) == {}  # current untouched (identity)


def test_absent_stamp_sidecar_is_skipped(tmp_path) -> None:
    conn = duckdb.connect()
    ensure_schema(conn)
    files_dir = tmp_path / "files"
    _write_sidecar(files_dir, "S", "a.txt", version=None)  # unstamped, no stamp
    assert scan_sidecars(conn, files_dir) == 0  # absent stamp → refused, not cataloged


def test_newer_stamp_sidecar_defers_then_heals(tmp_path, monkeypatch) -> None:
    # A sidecar stamped NEWER than this daemon knows is deferred (§1/#43), NOT
    # permanently skipped like an absent stamp: the presence-only catalog leaves
    # it un-cataloged, and because it's never mis-adapted through identity, a
    # newer daemon that knows the version re-reads the SAME sidecar and catalogs
    # it. The heal (0 → 1) is what distinguishes deferral from a permanent skip.
    conn = duckdb.connect()
    ensure_schema(conn)
    files_dir = tmp_path / "files"
    _write_sidecar(files_dir, "S", "future.txt", "2.0")
    assert scan_sidecars(conn, files_dir) == 0  # older daemon: newer stamp deferred

    _register_legacy(monkeypatch, SchemaStore.FILES, "2.0", lambda m: m)  # newer daemon knows 2.0
    assert scan_sidecars(conn, files_dir) == 1  # healed — same sidecar now catalogs
