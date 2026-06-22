"""F5 — backend-swap proof (req 6): FileStore works unchanged against a
NON-LOCAL blob backend.

We point a FileStore at a pyarrow ``_MockFileSystem`` (``is_local`` is
False, so every remote code path runs — staged upload via ``copy_files``,
``open_output_stream``, ranged ``open_input_file``, object PUT/GET/delete)
and drive the same operations the local suite uses. The catalog daemon is
the read path, so a URI resolves to a backend key with no local-disk
touch — exactly what lets the bytes live anywhere.

The point: only the backend handed to the store differs between local and
non-local. The store API and these assertions are identical to the local
tests — that is the swap.
"""

from __future__ import annotations

from uuid import uuid4

import pyarrow as pa
import pyarrow.fs as pafs
import pytest

from litmus.data.files import FileStore
from litmus.data.files._backend import BlobBackend


@pytest.fixture
def remote_store():
    """A FileStore whose blob backend is a non-local (mock) filesystem.

    No daemon: the ``file://{date}/{session}/{filename}`` URI carries the full
    backend key, so a point read is pure parsing → ``backend.read_bytes`` — it
    works against a remote backend with no catalog and no local walk. Swapping
    the backend is the ONLY thing that differs from the local suite; ``is_local``
    is False, so every remote code path runs.
    """
    store = FileStore()
    store._backend = BlobBackend(pafs._MockFileSystem(), "files")
    assert store._backend.is_local is False
    return store


class TestBackendSwap:
    def test_s3_uri_resolves_to_non_local_backend(self) -> None:
        """A config URI swap produces a non-local backend (the wiring)."""
        backend = BlobBackend.from_uri("s3://litmus-test-bucket/prefix?region=us-east-1")
        assert backend.is_local is False
        assert backend.local_path("2026/sid/x.bin") is None

    def test_one_shot_write_round_trips_on_non_local_backend(self, remote_store: FileStore) -> None:
        sid = str(uuid4())
        uri = remote_store.write("vendor_blob", b"raw bytes payload", session_id=sid)

        # Resolution goes through the daemon catalog (no local walk); bytes
        # come from the mock backend.
        assert remote_store.read(uri) == b"raw bytes payload"
        assert remote_store.size(uri) == len(b"raw bytes payload")
        assert remote_store.read_range(uri, offset=4, length=5) == b"bytes"
        meta = remote_store.read_attributes(uri)
        assert meta is not None and meta.size_bytes == len(b"raw bytes payload")

    def test_typed_value_round_trips_on_non_local_backend(self, remote_store: FileStore) -> None:
        sid = str(uuid4())
        tbl = pa.table({"v": [1.0, 2.0, 3.0]})
        uri = remote_store.write("scope.ch1.waveform", tbl, session_id=sid)
        assert uri.endswith(".arrow")

        data = remote_store.read(uri)
        assert data is not None
        loaded = pa.ipc.open_stream(pa.py_buffer(data)).read_all()
        assert loaded.column("v").to_pylist() == [1.0, 2.0, 3.0]

    def test_streaming_publishes_one_object_on_non_local_backend(
        self, remote_store: FileStore
    ) -> None:
        sid = str(uuid4())
        with remote_store.open_stream("daq", format="raw", session_id=sid) as sink:
            sink.write(b"first-")
            sink.write(b"second")
            uri = sink.uri

        # The stream completed as one object on the non-local backend.
        assert remote_store.read(uri) == b"first-second"
        assert remote_store.size(uri) == len(b"first-second")

    def test_delete_on_non_local_backend(self, remote_store: FileStore) -> None:
        sid = str(uuid4())
        uri = remote_store.write("scratch", b"bye", session_id=sid)
        assert remote_store.read(uri) == b"bye"
        remote_store.delete(uri)
        assert remote_store.read(uri) is None

    def test_jsonl_stream_round_trips_on_non_local_backend(self, remote_store: FileStore) -> None:
        sid = str(uuid4())
        with remote_store.open_stream("events", format="jsonl", session_id=sid) as sink:
            sink.write({"a": 1})
            sink.write({"b": 2})
            uri = sink.uri

        lines = (remote_store.read(uri) or b"").splitlines()
        assert lines == [b'{"a":1}', b'{"b":2}']
