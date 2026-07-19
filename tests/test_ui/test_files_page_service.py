"""Service-layer tests for the ``/files`` operator UI page.

The page (``testerkit.ui.pages.files.list``) reads through
``list_recent_files`` + the FileStore byte API. ``list_recent_files``
now reads exclusively through the files catalog daemon (no tree walk),
so these tests exercise it against the **shared canonical daemon** —
written artifacts are matched by their unique ``session_id`` the way
``test_files_catalog`` matches by uri. (A per-test daemon on a throwaway
dir would exhaust WSL's pid cgroup, so the store accumulates across
tests: assert membership, never exact totals.)

The store byte-API tests stay on a ``tmp_path`` store — FileStore reads
don't spawn a daemon.
"""

from __future__ import annotations

import time
from datetime import datetime
from uuid import uuid4

import pytest

from testerkit.data.data_dir import resolve_data_dir
from testerkit.data.files import FileStore, _reset_for_tests
from testerkit.data.files.catalog_manager import acquire, release
from testerkit.ui.shared import services

_CANONICAL = resolve_data_dir()


@pytest.fixture
def canonical_files():
    """Acquire the shared canonical files catalog daemon for the test.

    Writes go through a canonical ``FileStore``; ``list_recent_files``
    resolves to the same canonical dir, so the artifacts are visible.
    Per-test isolation is by a unique ``session_id``.
    """
    files_dir = _CANONICAL / "files"
    acquire(files_dir)
    _reset_for_tests()
    try:
        yield FileStore()
    finally:
        release(files_dir)


def _mine(sid: str, *, limit: int = 1000) -> list[dict]:
    """The ``list_recent_files`` rows belonging to this test's session."""
    return [e for e in services.list_recent_files(limit=limit) if e["session_id"] == sid]


def test_list_recent_files_empty_when_no_files_dir(tmp_path, monkeypatch) -> None:
    """No ``files/`` subdir → empty list (no daemon spawned, no walk)."""
    monkeypatch.setattr(services, "_resolve_data_dir", lambda: tmp_path)
    assert services.list_recent_files() == []


def test_list_recent_files_returns_written_artifact(canonical_files: FileStore) -> None:
    sid = str(uuid4())
    canonical_files.write("vendor_blob", b"raw bytes payload", session_id=sid)

    mine = _mine(sid)
    assert len(mine) == 1
    entry = mine[0]
    assert entry["filename"].startswith("vendor_blob")
    assert entry["uri"].startswith("file://") and entry["uri"].endswith(
        f"/{sid}/{entry['filename']}"
    )
    assert entry["size_bytes"] == len(b"raw bytes payload")
    assert isinstance(entry["created_at"], datetime)


def test_list_recent_files_excludes_sidecars(canonical_files: FileStore) -> None:
    sid = str(uuid4())
    canonical_files.write("foo", b"x", session_id=sid)
    mine = _mine(sid)
    assert mine and all(not e["filename"].endswith(".meta.json") for e in mine)


def test_list_recent_files_sorts_newest_first(canonical_files: FileStore) -> None:
    sid = str(uuid4())
    canonical_files.write("first", b"a", session_id=sid)
    # A real time gap guarantees distinct created_at → deterministic order.
    time.sleep(0.02)
    canonical_files.write("second", b"b", session_id=sid)

    stems = [e["filename"].split(".")[0] for e in _mine(sid)]
    assert stems == ["second", "first"]


def test_list_recent_files_caps_at_limit(canonical_files: FileStore) -> None:
    sid = str(uuid4())
    for i in range(5):
        canonical_files.write(f"file_{i}", b"x", session_id=sid)
    # The limit caps the total returned regardless of how many exist.
    assert len(services.list_recent_files(limit=3)) == 3


def test_list_recent_files_carries_mime_from_sidecar(canonical_files: FileStore) -> None:
    """The sidecar's mime makes it into the table cell."""
    from pydantic import BaseModel

    class Sample(BaseModel):
        key: str

    sid = str(uuid4())
    canonical_files.write("data", Sample(key="value"), session_id=sid)

    mine = _mine(sid)
    assert len(mine) == 1
    assert mine[0]["mime"] == "application/json"


def test_store_reads_back_existing_artifact(tmp_path) -> None:
    """FileStore byte API round-trips without a daemon (tmp_path store)."""
    _reset_for_tests()
    store = FileStore(_data_dir=tmp_path)
    sid = str(uuid4())
    uri = store.write("art", b"x", session_id=sid)

    assert store.size(uri) is not None
    assert store.read(uri) == b"x"


def test_store_returns_none_for_missing(tmp_path) -> None:
    _reset_for_tests()
    store = FileStore(_data_dir=tmp_path)
    assert store.read(f"file://{uuid4()}/ghost.bin") is None
