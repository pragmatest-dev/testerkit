"""Build item 1d — unify the two _ref dirs into FileStore.

Pre-1d: two separate ref locations on disk —

- ``events/{session_id}_ref/`` — used by ``EventLog.save_ref`` for
  pre-Position-2 raw-blob event payloads
- ``runs/{stem}_ref/`` — per-parquet sidecar used by
  ``ParquetBackend._save_file`` and ``materialize_channel_refs``

Post-1d: one canonical home ``files/{date}/{session_id}/{filename}``
(FileStore), reached by ``FileStore.put``. URI shape:
``file://{session_id}/{filename}`` (logical reference).

The portable bundle is now:
``runs/{date}/{stem}.parquet`` + ``files/{date}/{session_id}/``

Readers stay **dual-path** for legacy data lifetime:
``file://_ref/{filename}`` URIs in pre-1d parquets resolve to the
per-parquet sibling dir; new URIs resolve via FileStore.

Per CLAUDE.md test conventions: ``tmp_path``-backed FileStore
(FileStore writes don't spawn daemons).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from testerkit.data.backends._row_helpers import save_ref_to_dir
from testerkit.data.backends.parquet import (
    _resolve_ref_to_path,
    load_file,
    load_ref,
)
from testerkit.data.files import FileStore, _reset_for_tests, find_serializer


def _sid() -> str:
    return f"test-{uuid4().hex[:12]}"


@pytest.fixture
def filestore(tmp_path: Path, monkeypatch) -> FileStore:
    """Per-test FileStore singleton bound to tmp_path."""
    from testerkit.data.files import store as store_module

    monkeypatch.setattr(store_module, "resolve_data_dir", lambda _=None: tmp_path)
    _reset_for_tests()
    return FileStore()


# --------------------------------------------------------------------- #
# Arrow Table serializer (added in 1d for materialize_channel_refs)     #
# --------------------------------------------------------------------- #


class TestArrowTableSerializer:
    def test_arrow_table_resolves_to_arrow_serializer(self) -> None:
        tbl = pa.table({"value": [1.0, 2.0, 3.0]})
        s = find_serializer(tbl)
        assert s.extension == ".arrow"
        assert s.mime == "application/vnd.apache.arrow.stream"

    def test_arrow_table_round_trips_via_filestore(self, filestore: FileStore) -> None:
        sid = _sid()
        tbl = pa.table({"timestamp": [1, 2, 3], "value": [1.0, 2.0, 3.0]})

        uri = filestore.write("scope.ch1.waveform", tbl, session_id=sid)
        assert f"/{sid}/" in uri and uri.startswith("file://")
        assert uri.endswith(".arrow")

        data = filestore.read(uri)
        assert data is not None
        loaded = ipc.open_stream(pa.py_buffer(data)).read_all()
        assert loaded.num_rows == 3
        assert loaded.column("value").to_pylist() == [1.0, 2.0, 3.0]


# --------------------------------------------------------------------- #
# Write-side: save_ref_to_dir + FileStore both write through the         #
# same serializer registry                                              #
# --------------------------------------------------------------------- #


class TestUnifiedDispatch:
    def test_filestore_and_save_ref_share_registry_for_arrow_table(
        self, filestore: FileStore, tmp_path: Path
    ) -> None:
        """Legacy save_ref_to_dir gets the Arrow Table serializer too."""
        tbl = pa.table({"v": [1.0]})
        ref_dir = tmp_path / "legacy_ref"
        ref_dir.mkdir()
        uri = save_ref_to_dir(ref_dir, "vec00000", "wave", tbl)

        assert uri.endswith(".arrow")
        artifact = ref_dir / "vec00000_wave.arrow"
        assert artifact.exists()


# --------------------------------------------------------------------- #
# Read-side dual-path: load_file resolves both legacy + new URIs        #
# --------------------------------------------------------------------- #


class TestDualPathRead:
    def test_load_file_resolves_legacy_ref_uri(self, tmp_path: Path) -> None:
        """Legacy ``file://_ref/{filename}`` resolves relative to parquet path."""
        parquet_path = tmp_path / "runs" / "2026-05-31" / "run.parquet"
        ref_dir = parquet_path.parent / (parquet_path.stem + "_ref")
        ref_dir.mkdir(parents=True)
        (ref_dir / "abc12345_data.bin").write_bytes(b"legacy-bytes")

        result = load_file(parquet_path, "file://_ref/abc12345_data.bin")
        assert result == b"legacy-bytes"

    def test_load_file_resolves_new_filestore_uri(self, filestore: FileStore) -> None:
        """New ``file://{session_id}/{filename}`` resolves via FileStore."""
        sid = _sid()
        uri = filestore.write("payload", b"new-bytes", session_id=sid)

        # parquet_path is irrelevant for new URIs — pass any
        result = load_file(Path("/tmp/whatever.parquet"), uri)
        assert result == b"new-bytes"

    def test_load_file_returns_ref_for_unresolvable_uri(self, filestore: FileStore) -> None:
        """Unresolvable URI returns the ref string unchanged."""
        result = load_file(Path("/tmp/whatever.parquet"), "file://nosuch/missing.bin")
        assert result == "file://nosuch/missing.bin"

    def test_load_file_returns_ref_for_non_file_uri(self) -> None:
        """A channel:// URI isn't a file ref; load_file returns it as-is."""
        result = load_file(Path("/tmp/whatever.parquet"), "channel://x?session=abc")
        assert result == "channel://x?session=abc"


class TestResolveRefToPath:
    def test_resolves_legacy_ref_path(self, tmp_path: Path) -> None:
        parquet_path = tmp_path / "runs" / "x.parquet"
        ref_dir = parquet_path.parent / "x_ref"
        ref_dir.mkdir(parents=True)
        (ref_dir / "blob.bin").write_bytes(b"ok")

        path = _resolve_ref_to_path(parquet_path, "file://_ref/blob.bin")
        assert path == ref_dir / "blob.bin"

    def test_resolves_legacy_ref_without_file_prefix(self, tmp_path: Path) -> None:
        parquet_path = tmp_path / "runs" / "x.parquet"
        path = _resolve_ref_to_path(parquet_path, "_ref/blob.bin")
        assert path is not None
        assert path.name == "blob.bin"

    def test_filestore_uri_read_as_bytes_not_path(self, filestore: FileStore) -> None:
        # FileStore refs are no longer path-resolved (no layout Path crosses
        # out) — load_file reads them as bytes through the blob backend.
        sid = _sid()
        uri = filestore.write("x", b"y", session_id=sid)
        assert _resolve_ref_to_path(None, uri) is None
        assert load_file(None, uri) == b"y"

    def test_returns_none_for_non_file_ref(self) -> None:
        assert _resolve_ref_to_path(None, "channel://x?session=abc") is None
        assert _resolve_ref_to_path(None, "not-a-ref") is None

    def test_legacy_ref_without_parquet_path_returns_none(self) -> None:
        """Legacy URI shape requires parquet_path; without it, can't resolve."""
        assert _resolve_ref_to_path(None, "file://_ref/blob.bin") is None


# --------------------------------------------------------------------- #
# load_ref dispatch — file:// scheme works without parquet_path for     #
# new-shape URIs                                                        #
# --------------------------------------------------------------------- #


class TestLoadRefDispatch:
    def test_new_file_uri_works_without_parquet_path(self, filestore: FileStore) -> None:
        """Item 1d: FileStore URIs are self-resolving."""
        sid = _sid()
        uri = filestore.write("x", b"data", session_id=sid)

        # No parquet_path needed for new URIs
        result = load_ref(uri, parquet_path=None)
        assert result == b"data"

    def test_legacy_file_uri_with_parquet_path_still_works(self, tmp_path: Path) -> None:
        parquet_path = tmp_path / "runs" / "legacy.parquet"
        ref_dir = parquet_path.parent / "legacy_ref"
        ref_dir.mkdir(parents=True)
        (ref_dir / "v1_data.bin").write_bytes(b"legacy")

        result = load_ref("file://_ref/v1_data.bin", parquet_path=parquet_path)
        assert result == b"legacy"


# --------------------------------------------------------------------- #
# EventLog: save_ref + _ref_dir removed (vestigial under Position 2)    #
# --------------------------------------------------------------------- #


class TestEventLogVestigialRemoval:
    def test_eventlog_has_no_save_ref_method(self) -> None:
        """Pre-Position-2 ``save_ref`` is removed (item 1d cleanup).

        All blob storage now routes through FileStore at the verb
        layer; EventLog has nothing of its own to claim-check.
        """
        from testerkit.data.event_log import EventLog

        assert not hasattr(EventLog, "save_ref")

    def test_eventlog_has_no_ref_dir_attribute(self, tmp_path: Path) -> None:
        from testerkit.data.event_log import EventLog

        log = EventLog(tmp_path / "events", uuid4())
        try:
            assert not hasattr(log, "_ref_dir")
        finally:
            log.close()


# --------------------------------------------------------------------- #
# ParquetBackend: _get_ref_dir removed                                  #
# --------------------------------------------------------------------- #


class TestParquetBackendVestigialRemoval:
    def test_parquet_backend_has_no_get_ref_dir(self) -> None:
        from testerkit.data.backends.parquet import ParquetBackend

        assert not hasattr(ParquetBackend, "_get_ref_dir")

    def test_parquet_backend_has_no_save_file(self) -> None:
        from testerkit.data.backends.parquet import ParquetBackend

        assert not hasattr(ParquetBackend, "_save_file")
