"""As-if-real tests for the migrate sink (``schema_migrate``).

Not monkeypatch-of-internals: a genuine reshape adapter is registered for a
synthetic *older* version, applied to a REAL file on disk, atomically rewritten
forward, and re-read to confirm the re-stamp AND the transform. The only fiction
is the version string ("0.9") and the transform — the file I/O, the adapter
application, the atomic swap, and the re-read are all real. This is the "make
everything except the version-and-transform real" strategy: when a real 2.0
lands, only the transform changes.
"""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data import schema_dispatch, schema_versions
from litmus.data.files.models import FileArtifactMetadata
from litmus.data.schema_dispatch import SchemaVersionRefused
from litmus.data.schema_migrate import migrate_parquet_file, migrate_sidecar_file
from litmus.data.schema_versions import SchemaStore
from litmus.data.schemas import RUN_ROW_SCHEMA


def _register_legacy(monkeypatch, store: SchemaStore, version: str, adapter) -> None:
    """Make *version* a known, adapter-backed legacy version for *store* — the
    _LEGACY_READABLE scenario, without touching the shipped registry."""
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        store,
        schema_versions.KNOWN_SCHEMA_VERSIONS[store] | {version},
    )
    monkeypatch.setitem(schema_dispatch._ADAPTERS[store], version, adapter)


def _write_run_parquet(path, version: str | None) -> None:
    row: dict[str, Any] = {f.name: None for f in RUN_ROW_SCHEMA}
    row["record_type"] = "run"
    row["run_id"] = "R-MIGRATE"
    row["run_outcome"] = "passed"
    table = pa.table({k: [v] for k, v in row.items()}, schema=RUN_ROW_SCHEMA)
    metadata = {} if version is None else {b"schema_version": version.encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)


class TestMigrateParquet:
    @pytest.fixture
    def legacy_runs_adapter(self, monkeypatch):
        # Real reshape: rewrite run_outcome to a marker so we can prove the
        # transform ran (stands in for a real 0.9 -> current column reshape).
        def reshape(table: pa.Table) -> pa.Table:
            idx = table.schema.get_field_index("run_outcome")
            return table.set_column(idx, "run_outcome", pa.array(["MIGRATED"] * table.num_rows))

        _register_legacy(monkeypatch, SchemaStore.RUNS, "0.9", reshape)

    def test_migrate_reshapes_and_restamps(self, tmp_path, legacy_runs_adapter):
        p = tmp_path / "old.parquet"
        _write_run_parquet(p, "0.9")
        assert migrate_parquet_file(SchemaStore.RUNS, p) is True
        pf = pq.ParquetFile(str(p))
        assert pf.schema_arrow.metadata[b"schema_version"] == b"1.0"  # re-stamped
        assert pf.read().column("run_outcome").to_pylist() == ["MIGRATED"]  # transform applied

    def test_migrate_current_version_is_noop(self, tmp_path):
        p = tmp_path / "current.parquet"
        _write_run_parquet(p, "1.0")
        assert migrate_parquet_file(SchemaStore.RUNS, p) is False

    def test_migrate_refuses_permanent_version(self, tmp_path):
        p = tmp_path / "absent.parquet"
        _write_run_parquet(p, None)
        with pytest.raises(SchemaVersionRefused):
            migrate_parquet_file(SchemaStore.RUNS, p)


class TestMigrateSidecar:
    @pytest.fixture
    def legacy_files_adapter(self, monkeypatch):
        def reshape(meta: FileArtifactMetadata) -> FileArtifactMetadata:
            return meta.model_copy(
                update={"attributes": {**meta.attributes, "migrated_from": "0.9"}}
            )

        _register_legacy(monkeypatch, SchemaStore.FILES, "0.9", reshape)

    def test_migrate_reshapes_and_restamps(self, tmp_path, legacy_files_adapter):
        p = tmp_path / "artifact.txt.meta.json"
        meta = FileArtifactMetadata(
            mime="text/plain", extension="txt", size_bytes=3, schema_version="0.9"
        )
        p.write_text(meta.model_dump_json())
        assert migrate_sidecar_file(p) is True
        after = json.loads(p.read_text())
        assert after["schema_version"] == "1.0"  # re-stamped
        assert after["attributes"]["migrated_from"] == "0.9"  # transform applied

    def test_migrate_current_version_is_noop(self, tmp_path):
        p = tmp_path / "current.txt.meta.json"
        # Default schema_version is the current version.
        p.write_text(
            FileArtifactMetadata(mime="text/plain", extension="txt", size_bytes=3).model_dump_json()
        )
        assert migrate_sidecar_file(p) is False
