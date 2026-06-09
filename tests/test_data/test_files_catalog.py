"""Files catalog daemon — warm-index resolve/list over the canonical dir.

Like ``test_channel_server.TestDaemonLifecycle``, these point at the
canonical files dir so they share one catalog daemon for the file
(spawning a fresh daemon per test hits WSL's pids cgroup). Per-test
isolation is by unique artifact names + session ids.
"""

from __future__ import annotations

import time
from uuid import uuid4

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files.catalog_manager import (
    acquire,
    list_recent,
    release,
    resolve_uri,
    subscribe_frames,
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

            # The live write pushed into the warm catalog; resolve returns the
            # backend key, which under the local backend lives at files_dir/key.
            key = resolve_uri(files_dir, uri)
            assert key is not None
            blob = files_dir / key
            assert blob.exists()
            assert blob.read_bytes() == b"hello-bytes"
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


class TestStreamFramePush:
    """Ephemeral frame push (req 5): a streaming sink fans out a
    non-persisted frame per write; live subscribers range-read the new
    window. Frames are NOT durable events (the event log stays
    lifecycle-only).
    """

    def test_sink_write_publishes_frame(self) -> None:
        files_dir = _CANONICAL / "files"
        acquire(files_dir)
        received: list[dict] = []
        unsub = subscribe_frames(files_dir, received.append)
        try:
            store = FileStore()
            sid = uuid4().hex
            with store.open_stream(
                f"cat.stream.{uuid4().hex[:8]}", format="raw", session_id=sid
            ) as sink:
                # Write until a frame lands — tolerates the brief window
                # before the subscriber's stream attaches (frames are
                # ephemeral, missed-before-attach is expected). No flake:
                # we keep producing until one arrives or we time out.
                deadline = time.monotonic() + 5.0
                while not received and time.monotonic() < deadline:
                    sink.write(b"xxxx")
                    time.sleep(0.05)
                assert received, "no frame notification arrived"
                frame = received[0]
                assert frame["length"] == 4
                assert frame["uri"] == sink.uri
                assert frame["byte_offset"] >= 0
        finally:
            unsub()
            release(files_dir)


class TestFileRangeRead:
    """GET /api/files honors HTTP Range so consumers range-read a large
    or still-growing artifact without re-fetching the whole file.
    """

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from litmus.api.app import create_api_router

        app = FastAPI()
        app.include_router(create_api_router())
        return TestClient(app)

    def test_full_and_partial_reads(self) -> None:
        store = FileStore()
        sid = uuid4().hex
        uri = store.write(f"range.{uuid4().hex[:8]}", b"0123456789", session_id=sid)
        client = self._client()

        full = client.get("/api/files", params={"uri": uri})
        assert full.status_code == 200
        assert full.content == b"0123456789"
        assert full.headers["accept-ranges"] == "bytes"

        part = client.get("/api/files", params={"uri": uri}, headers={"Range": "bytes=2-5"})
        assert part.status_code == 206
        assert part.content == b"2345"
        assert part.headers["content-range"] == "bytes 2-5/10"

        tail = client.get("/api/files", params={"uri": uri}, headers={"Range": "bytes=7-"})
        assert tail.status_code == 206
        assert tail.content == b"789"

    def test_unsatisfiable_range_416(self) -> None:
        store = FileStore()
        sid = uuid4().hex
        uri = store.write(f"range.{uuid4().hex[:8]}", b"short", session_id=sid)
        client = self._client()
        resp = client.get("/api/files", params={"uri": uri}, headers={"Range": "bytes=100-200"})
        assert resp.status_code == 416
