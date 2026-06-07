"""Files catalog daemon — warm-index resolve/list over the canonical dir.

Like ``test_channel_server.TestDaemonLifecycle``, these point at the
canonical files dir so they share one catalog daemon for the file
(spawning a fresh daemon per test hits WSL's pids cgroup). Per-test
isolation is by unique artifact names + session ids.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files.catalog_manager import (
    acquire,
    list_recent,
    release,
    resolve_uri,
)
from litmus.data.files.store import FileStore

_CANONICAL = resolve_data_dir()


class TestFilesCatalogDaemon:
    def test_write_then_resolve_via_daemon(self) -> None:
        files_dir = _CANONICAL / "files"
        acquire(files_dir)
        try:
            store = FileStore()
            sid = uuid4().hex
            name = f"cat.resolve.{uuid4().hex[:8]}"
            uri = store.write(name, b"hello-bytes", session_id=sid)

            # The live write pushed into the warm catalog; resolve hits it.
            path = resolve_uri(files_dir, uri)
            assert path is not None
            assert Path(path).exists()
            assert Path(path).read_bytes() == b"hello-bytes"
        finally:
            release(files_dir)

    def test_list_recent_via_daemon(self) -> None:
        files_dir = _CANONICAL / "files"
        acquire(files_dir)
        try:
            store = FileStore()
            sid = uuid4().hex
            uri = store.write(f"cat.list.{uuid4().hex[:8]}", b"data", session_id=sid)

            rows = list_recent(files_dir, limit=1000)
            assert uri in {r["uri"] for r in rows}
        finally:
            release(files_dir)

    def test_resolve_missing_returns_none(self) -> None:
        files_dir = _CANONICAL / "files"
        acquire(files_dir)
        try:
            missing = resolve_uri(files_dir, f"file://{uuid4().hex}/nope.bin")
            assert missing is None
        finally:
            release(files_dir)
