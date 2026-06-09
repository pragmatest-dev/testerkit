"""Service-layer tests for the ``/files`` operator UI page.

The page itself (``litmus.ui.pages.files.list``) is a thin wrapper
around ``list_recent_files`` + the FileStore byte API + the
``/files-static/...`` route. These tests exercise the service
functions against a synthesized FileStore layout on disk so the
contract that powers the table cells is locked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.files import FileStore, _reset_for_tests
from litmus.ui.shared import services


@pytest.fixture
def isolated_filestore(tmp_path, monkeypatch) -> FileStore:
    """Fresh FileStore singleton bound to ``tmp_path``.

    ``services.list_recent_files`` reads through ``resolve_data_dir``
    (no project override → platformdirs default). Patch the singleton
    factory + the resolver service helper so the test sees an
    isolated dir.
    """
    _reset_for_tests()
    monkeypatch.setattr(services, "_resolve_data_dir", lambda: tmp_path)

    store = FileStore(data_dir=tmp_path)

    def _get() -> FileStore:
        return store

    from litmus.data import files as files_module

    monkeypatch.setattr(files_module, "_filestore", store)
    monkeypatch.setattr(files_module, "get_filestore", _get)
    return store


def test_list_recent_files_empty_when_no_files_dir(tmp_path, monkeypatch) -> None:
    """No ``files/`` subdir → empty list (no crash)."""
    monkeypatch.setattr(services, "_resolve_data_dir", lambda: tmp_path)
    _reset_for_tests()
    monkeypatch.setattr("litmus.data.files._filestore", FileStore(data_dir=tmp_path))
    assert services.list_recent_files() == []


def test_list_recent_files_returns_written_artifact(isolated_filestore: FileStore) -> None:
    sid = str(uuid4())
    isolated_filestore.write("vendor_blob", b"raw bytes payload", session_id=sid)

    entries = services.list_recent_files()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_id"] == sid
    assert entry["filename"].startswith("vendor_blob")
    assert entry["uri"] == f"file://{sid}/{entry['filename']}"
    assert entry["size_bytes"] == len(b"raw bytes payload")
    assert isinstance(entry["created_at"], datetime)


def test_list_recent_files_excludes_sidecars(isolated_filestore: FileStore) -> None:
    sid = str(uuid4())
    isolated_filestore.write("foo", b"x", session_id=sid)
    entries = services.list_recent_files()
    assert all(not e["filename"].endswith(".meta.json") for e in entries)


def test_list_recent_files_sorts_newest_first(isolated_filestore: FileStore) -> None:
    sid = str(uuid4())
    # Two artifacts with distinct mtimes by sleeping is brittle; touch
    # one to the future so ordering is deterministic.
    isolated_filestore.write("first", b"a", session_id=sid)
    isolated_filestore.write("second", b"b", session_id=sid)

    older_path = next(iter(Path(isolated_filestore._files_dir).rglob("first*")))
    newer_path = next(iter(Path(isolated_filestore._files_dir).rglob("second*")))
    import os

    older_ts = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    newer_ts = datetime(2026, 6, 1, tzinfo=UTC).timestamp()
    os.utime(older_path, (older_ts, older_ts))
    os.utime(newer_path, (newer_ts, newer_ts))

    entries = services.list_recent_files()
    assert [e["filename"].split(".")[0] for e in entries] == ["second", "first"]


def test_list_recent_files_caps_at_limit(isolated_filestore: FileStore) -> None:
    sid = str(uuid4())
    for i in range(5):
        isolated_filestore.write(f"file_{i}", b"x", session_id=sid)

    entries = services.list_recent_files(limit=3)
    assert len(entries) == 3


def test_store_reads_back_existing_artifact(isolated_filestore: FileStore) -> None:
    sid = str(uuid4())
    uri = isolated_filestore.write("art", b"x", session_id=sid)

    assert isolated_filestore.size(uri) is not None
    assert isolated_filestore.read(uri) == b"x"


def test_store_returns_none_for_missing(isolated_filestore: FileStore) -> None:
    assert isolated_filestore.read(f"file://{uuid4()}/ghost.bin") is None


def test_list_recent_files_carries_mime_from_sidecar(isolated_filestore: FileStore) -> None:
    """The sidecar's mime makes it into the table cell."""
    from pydantic import BaseModel

    class Sample(BaseModel):
        key: str

    sid = str(uuid4())
    isolated_filestore.write("data", Sample(key="value"), session_id=sid)

    entries = services.list_recent_files()
    assert len(entries) == 1
    assert entries[0]["mime"] == "application/json"
