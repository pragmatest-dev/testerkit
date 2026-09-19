"""``testerkit forward --channels``/``--files`` — unit tests (no live server).

Mirrors ``tests/test_cli/test_forward.py``'s style: the HTTP POST is
monkeypatched, real channel/file store artifacts are written to a tmp dir (via
``ChannelStore``/``FileStore`` with no daemon — see those tests' module
docstring), and the pure cursor / wire-shape / dedupe logic is exercised
directly. No live server, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pytest

from testerkit.cli import forward_cmd
from testerkit.data.channels.store import ChannelStore
from testerkit.data.files.store import FileStore
from testerkit.replication import ChannelSegment, FileRecord, read_closed_channel_segments


def _one_channel_segment(tmp_path: Path, channel_id: str = "psu.voltage") -> ChannelSegment:
    store = ChannelStore(tmp_path, uuid4())
    store.write(channel_id, 3.3, unit="V")
    store.close()
    segments = read_closed_channel_segments(tmp_path / "channels", sent=set())
    assert len(segments) == 1
    return segments[0]


# --------------------------------------------------------------------------- #
# Cursor persistence (generic JSON + channels/files-specific shapes)          #
# --------------------------------------------------------------------------- #


def test_load_json_missing_file_returns_empty(tmp_path: Path) -> None:
    assert forward_cmd._load_json(tmp_path / "nope.json") == {}


def test_events_cursor_roundtrip_unchanged(tmp_path: Path) -> None:
    """The original events cursor helpers keep their exact behavior after the
    _load_json/_save_json refactor."""
    path = tmp_path / "cursor.json"
    forward_cmd._save_cursor(path, {"w0": 5, "w1": 2})
    assert forward_cmd._load_cursor(path) == {"w0": 5, "w1": 2}


def test_events_cursor_malformed_values_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"w0": "not-an-int"}))
    assert forward_cmd._load_cursor(path) == {}


def test_channels_cursor_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    forward_cmd._save_channels_cursor(path, {"2026-09-14/a_deadbeef.arrow"})
    assert forward_cmd._load_channels_cursor(path) == {"2026-09-14/a_deadbeef.arrow"}


def test_channels_cursor_missing_file_is_empty_set(tmp_path: Path) -> None:
    assert forward_cmd._load_channels_cursor(tmp_path / "nope.json") == set()


def test_files_cursor_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    forward_cmd._save_files_cursor(path, {"file://a"}, {"abc123"})
    uris, hashes = forward_cmd._load_files_cursor(path)
    assert uris == {"file://a"}
    assert hashes == {"abc123"}


def test_files_cursor_missing_file_is_empty_sets(tmp_path: Path) -> None:
    uris, hashes = forward_cmd._load_files_cursor(tmp_path / "nope.json")
    assert uris == set()
    assert hashes == set()


# --------------------------------------------------------------------------- #
# Channel segment wire shape                                                   #
# --------------------------------------------------------------------------- #


def test_channel_wire_table_scalar_passthrough(tmp_path: Path) -> None:
    seg = _one_channel_segment(tmp_path, "psu.voltage")
    wire = forward_cmd._channel_wire_table(seg)

    assert wire.num_rows == 1
    # Real testerkit segment shape (docs/25 re-alignment) — the columns
    # `ChannelIndex` reads; `channel_id` rides in the URL, `value_type`/`units`
    # ride in the ChannelDescriptor schema metadata (not columns).
    assert set(wire.column_names) == {
        "received_at",
        "sampled_at",
        "value",
        "source_method",
        "session_id",
        "sample_interval",
        "sample_offset",
    }
    assert wire.column("value").to_pylist() == [3.3]  # native float, not JSON-wrapped
    assert wire.column("sample_offset").to_pylist() == [0]
    assert (wire.schema.metadata or {}).get(b"testerkit.channel_descriptor") is not None


def test_channel_wire_table_struct_value_is_json_encoded(tmp_path: Path) -> None:
    store = ChannelStore(tmp_path, uuid4())
    store.write("scope.trace", {"a": 1, "b": 2})
    store.close()
    seg = read_closed_channel_segments(tmp_path / "channels", sent=set())[0]

    wire = forward_cmd._channel_wire_table(seg)
    assert wire.num_rows == 1
    # struct channel (no native `value` column) → JSON-encoded, exactly as
    # ChannelIndex encodes it at rest, so the server's decode_value_column
    # round-trips it.
    value = wire.column("value").to_pylist()[0]
    assert json.loads(value) == {"a": 1, "b": 2}
    assert (wire.schema.metadata or {}).get(b"testerkit.channel_descriptor") is not None


def test_channel_wire_table_array_passthrough(tmp_path: Path) -> None:
    store = ChannelStore(tmp_path, uuid4())
    store.write("scope.waveform", [1.0, 2.0, 3.0], sample_interval=0.001)
    store.close()
    seg = read_closed_channel_segments(tmp_path / "channels", sent=set())[0]

    wire = forward_cmd._channel_wire_table(seg)
    # array channel: native list `value` passed through unchanged (one row per
    # capture), sample_interval preserved.
    assert wire.column("value").to_pylist() == [[1.0, 2.0, 3.0]]
    assert wire.column("sample_interval").to_pylist() == [0.001]


# --------------------------------------------------------------------------- #
# _forward_channels_once                                                       #
# --------------------------------------------------------------------------- #


def test_forward_channels_once_nothing_new_returns_none(tmp_path: Path, monkeypatch) -> None:
    channels_dir = tmp_path / "channels"
    channels_dir.mkdir()

    def _boom(*a, **k):
        raise AssertionError("should not POST when there is nothing to forward")

    monkeypatch.setattr(forward_cmd, "_post_channel_segment", _boom)
    result = forward_cmd._forward_channels_once(
        channels_dir, tmp_path / "c.json", "http://x", "tk", timeout=5.0
    )
    assert result is None


def test_forward_channels_once_posts_and_advances_cursor(tmp_path: Path, monkeypatch) -> None:
    store = ChannelStore(tmp_path, uuid4())
    store.write("psu.voltage", 3.3)
    store.close()
    channels_dir = tmp_path / "channels"
    cursor_path = tmp_path / "cursor.json"

    posted: list[str] = []

    def _fake_post(url, token, channel_id, table, *, timeout):
        posted.append(channel_id)
        return {
            "segment_key": "orgs/x/channels/psu.voltage/abc.parquet",
            "row_count": table.num_rows,
        }

    monkeypatch.setattr(forward_cmd, "_post_channel_segment", _fake_post)
    result = forward_cmd._forward_channels_once(
        channels_dir, cursor_path, "http://x", "tk", timeout=5.0
    )

    assert result == {"segments": 1, "rows": 1}
    assert posted == ["psu.voltage"]
    cursor = forward_cmd._load_channels_cursor(cursor_path)
    assert len(cursor) == 1

    # Re-run: the segment is already in the cursor, so it must not be re-sent.
    monkeypatch.setattr(
        forward_cmd,
        "_post_channel_segment",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resend")),
    )
    assert (
        forward_cmd._forward_channels_once(channels_dir, cursor_path, "http://x", "tk", timeout=5.0)
        is None
    )


def test_forward_channels_once_does_not_advance_cursor_on_post_failure(
    tmp_path: Path, monkeypatch
) -> None:
    store = ChannelStore(tmp_path, uuid4())
    store.write("psu.voltage", 3.3)
    store.close()
    channels_dir = tmp_path / "channels"
    cursor_path = tmp_path / "cursor.json"

    def _fail(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(forward_cmd, "_post_channel_segment", _fail)
    with pytest.raises(OSError):
        forward_cmd._forward_channels_once(channels_dir, cursor_path, "http://x", "tk", timeout=5.0)

    # No cursor file was ever written -- the failed segment will be retried.
    assert forward_cmd._load_channels_cursor(cursor_path) == set()


def test_forward_channels_once_persists_cursor_per_segment(tmp_path: Path, monkeypatch) -> None:
    """The second of two segments fails to POST -- the first must already be
    durably recorded (persist-per-segment, not batched at the end)."""
    store = ChannelStore(tmp_path, uuid4())
    store.write("psu.voltage", 1.0)
    store.close()
    store2 = ChannelStore(tmp_path, uuid4())
    store2.write("dmm.current", 2.0)
    store2.close()
    channels_dir = tmp_path / "channels"
    cursor_path = tmp_path / "cursor.json"

    calls = []

    def _flaky_post(url, token, channel_id, table, *, timeout):
        calls.append(channel_id)
        if len(calls) == 2:
            raise OSError("network down")
        return {"segment_key": "k", "row_count": table.num_rows}

    monkeypatch.setattr(forward_cmd, "_post_channel_segment", _flaky_post)
    with pytest.raises(OSError):
        forward_cmd._forward_channels_once(channels_dir, cursor_path, "http://x", "tk", timeout=5.0)

    cursor = forward_cmd._load_channels_cursor(cursor_path)
    assert len(cursor) == 1  # only the first (successful) segment was recorded


# --------------------------------------------------------------------------- #
# _forward_files_once                                                          #
# --------------------------------------------------------------------------- #


def test_forward_files_once_nothing_new_returns_none(tmp_path: Path, monkeypatch) -> None:
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    def _boom(*a, **k):
        raise AssertionError("should not POST when there is nothing to forward")

    monkeypatch.setattr(forward_cmd, "_post_file_blob", _boom)
    assert (
        forward_cmd._forward_files_once(
            files_dir, tmp_path / "c.json", "http://x", "tk", timeout=5.0
        )
        is None
    )


def test_forward_files_once_posts_and_advances_cursor(tmp_path: Path, monkeypatch) -> None:
    store = FileStore(_data_dir=tmp_path)
    store.write("capture", b"hello", session_id=str(uuid4()))
    files_dir = tmp_path / "files"
    cursor_path = tmp_path / "cursor.json"

    posted: list[bytes] = []

    def _fake_post(url, token, record, *, timeout):
        posted.append(record.data)
        return {"content_hash": "deadbeef", "inserted": True}

    monkeypatch.setattr(forward_cmd, "_post_file_blob", _fake_post)
    result = forward_cmd._forward_files_once(files_dir, cursor_path, "http://x", "tk", timeout=5.0)

    assert result == {"files": 1, "skipped_dupe": 0}
    assert posted == [b"hello"]
    sent_uris, sent_hashes = forward_cmd._load_files_cursor(cursor_path)
    assert len(sent_uris) == 1
    assert len(sent_hashes) == 1


def test_forward_files_once_skips_reupload_of_identical_content(
    tmp_path: Path, monkeypatch
) -> None:
    """Two different URIs with identical bytes: the second is retired into the
    cursor without a second POST (bandwidth optimization; the server would
    dedupe by hash anyway)."""
    store = FileStore(_data_dir=tmp_path)
    store.write("a", b"same-bytes", session_id=str(uuid4()))
    store.write("b", b"same-bytes", session_id=str(uuid4()))
    files_dir = tmp_path / "files"
    cursor_path = tmp_path / "cursor.json"

    calls = []
    monkeypatch.setattr(
        forward_cmd,
        "_post_file_blob",
        lambda url, token, record, *, timeout: calls.append(record.uri)
        or {"content_hash": "x", "inserted": True},
    )
    result = forward_cmd._forward_files_once(files_dir, cursor_path, "http://x", "tk", timeout=5.0)

    assert result == {"files": 1, "skipped_dupe": 1}
    assert len(calls) == 1  # only one actually POSTed
    sent_uris, _ = forward_cmd._load_files_cursor(cursor_path)
    assert len(sent_uris) == 2  # but both retired from future scans


def test_forward_files_once_does_not_advance_cursor_on_post_failure(
    tmp_path: Path, monkeypatch
) -> None:
    store = FileStore(_data_dir=tmp_path)
    store.write("capture", b"hello", session_id=str(uuid4()))
    files_dir = tmp_path / "files"
    cursor_path = tmp_path / "cursor.json"

    def _fail(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(forward_cmd, "_post_file_blob", _fail)
    with pytest.raises(OSError):
        forward_cmd._forward_files_once(files_dir, cursor_path, "http://x", "tk", timeout=5.0)

    sent_uris, sent_hashes = forward_cmd._load_files_cursor(cursor_path)
    assert sent_uris == set()
    assert sent_hashes == set()


def test_post_file_blob_builds_expected_multipart_body(monkeypatch) -> None:
    """Pins the wire shape this side targets (REVIEW NEEDED — unconfirmed
    against a real server, see forward_cmd module docstring): multipart with a
    JSON "meta" field and a binary "file" field."""
    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"content_hash": "h", "inserted": True}).encode()

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _FakeResponse()

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", _fake_urlopen)
    rec = FileRecord(
        uri="file://2026-09-14/s1/capture.bin",
        session_id="s1",
        name="capture.bin",
        data=b"hello",
        metadata=__import__(
            "testerkit.data.files.models", fromlist=["FileArtifactMetadata"]
        ).FileArtifactMetadata(mime="application/octet-stream", extension=".bin", size_bytes=5),
    )

    disp = forward_cmd._post_file_blob("http://x", "tk", rec, timeout=5.0)

    assert disp == {"content_hash": "h", "inserted": True}
    assert captured["url"] == "http://x/ingest/files"
    assert captured["headers"]["Authorization"] == "Bearer tk"
    assert captured["headers"]["Content-type"].startswith("multipart/form-data; boundary=")
    body = captured["body"]
    assert b'name="meta"' in body
    assert b'name="file"; filename="capture.bin"' in body
    assert b"hello" in body
    # The meta field is valid JSON carrying the documented fields.
    meta_start = body.index(b"\r\n\r\n") + 4
    meta_end = body.index(b"\r\n--", meta_start)
    meta = json.loads(body[meta_start:meta_end])
    assert meta["name"] == "capture.bin"
    assert meta["session_id"] == "s1"
    assert meta["step_path"] is None


def test_post_channel_segment_url_quotes_channel_id(monkeypatch) -> None:
    """Pins the wire shape this side targets (REVIEW NEEDED — unconfirmed
    against a real server): Arrow IPC POST to /ingest/channels/{channel_id}."""
    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"segment_key": "k", "row_count": 1}).encode()

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _FakeResponse()

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", _fake_urlopen)
    table = pa.table({"value": [1.0]})
    disp = forward_cmd._post_channel_segment(
        "http://x", "tk", "psu/voltage weird", table, timeout=5.0
    )

    assert disp == {"segment_key": "k", "row_count": 1}
    assert captured["url"] == "http://x/ingest/channels/psu%2Fvoltage%20weird"
    assert captured["headers"]["Authorization"] == "Bearer tk"


# --------------------------------------------------------------------------- #
# _forward_all_once — default-behavior-unchanged guarantee                    #
# --------------------------------------------------------------------------- #


def test_forward_all_once_default_flags_never_touch_channels_or_files(
    tmp_path: Path, monkeypatch
) -> None:
    """With channels=False, files=False (the CLI default), _forward_all_once
    must do exactly what the original events-only _forward_once did -- never
    even look at the channels/files dirs. This is the behavior-unchanged
    guarantee for a plain ``testerkit forward``."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()

    def _boom(*a, **k):
        raise AssertionError("must not be called when channels/files are disabled")

    monkeypatch.setattr(forward_cmd, "_forward_channels_once", _boom)
    monkeypatch.setattr(forward_cmd, "_forward_files_once", _boom)
    monkeypatch.setattr(forward_cmd, "_post_ingest", _boom)  # nothing to forward -> never called

    result = forward_cmd._forward_all_once(
        events_dir,
        tmp_path / "e.json",
        tmp_path / "channels",
        tmp_path / "c.json",
        tmp_path / "files",
        tmp_path / "f.json",
        "http://x",
        "tk",
        timeout=5.0,
        channels=False,
        files=False,
    )
    assert result == {}


def test_forward_all_once_runs_enabled_stores(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    channels_dir = tmp_path / "channels"
    files_dir = tmp_path / "files"

    monkeypatch.setattr(
        forward_cmd, "_forward_channels_once", lambda *a, **k: {"segments": 1, "rows": 1}
    )
    monkeypatch.setattr(
        forward_cmd, "_forward_files_once", lambda *a, **k: {"files": 1, "skipped_dupe": 0}
    )

    result = forward_cmd._forward_all_once(
        events_dir,
        tmp_path / "e.json",
        channels_dir,
        tmp_path / "c.json",
        files_dir,
        tmp_path / "f.json",
        "http://x",
        "tk",
        timeout=5.0,
        channels=True,
        files=True,
    )
    assert result == {
        "channels": {"segments": 1, "rows": 1},
        "files": {"files": 1, "skipped_dupe": 0},
    }
