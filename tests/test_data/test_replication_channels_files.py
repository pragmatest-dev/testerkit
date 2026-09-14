"""Unit tests for the channel-segment / file-blob forwarding readers (docs/22 Part B).

``read_closed_channel_segments`` and ``read_new_file_records`` are the sanctioned
direct readers a forwarder uses to discover what's new on disk — the channels/files
analogue of ``read_segments`` for the event WAL. These tests cover segment/record
selection and the "already sent" skip; the HTTP/cursor-persistence orchestration
lives in ``testerkit.cli.forward_cmd`` and is tested in
``tests/test_cli/test_forward_channels_files.py``.

Uses real ``ChannelStore``/``FileStore`` writers (both with ``serve=False`` / no
daemon — see module docstrings: neither spawns anything without an explicit
``serve=True`` / a daemon already running) so the on-disk shape under test is
authentic, not a hand-rolled guess at the schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc

from testerkit.data.channels.store import ChannelStore
from testerkit.data.files.store import FileStore
from testerkit.replication import read_closed_channel_segments, read_new_file_records


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# --------------------------------------------------------------------------- #
# Channel segments                                                             #
# --------------------------------------------------------------------------- #


def test_read_closed_channel_segments_reads_a_real_closed_segment(tmp_path: Path) -> None:
    sid = uuid4()
    store = ChannelStore(tmp_path, sid)
    store.write("psu.voltage", 3.3)
    store.write("psu.voltage", 3.31)
    store.close()  # flush + close -- the file is a complete, closed Arrow IPC stream

    channels_dir = tmp_path / "channels"
    segments = read_closed_channel_segments(channels_dir, sent=set())

    assert len(segments) == 1
    seg = segments[0]
    assert seg.channel_id == "psu.voltage"
    assert seg.table.num_rows == 2
    assert seg.rel_path == f"{_today()}/psu.voltage_{str(sid)[:8]}.arrow"


def test_read_closed_channel_segments_handles_underscored_channel_id(tmp_path: Path) -> None:
    """The filename regex is greedy on channel_id -- a channel id containing its
    own underscores must not be mis-split against the trailing session_short."""
    sid = uuid4()
    store = ChannelStore(tmp_path, sid)
    store.write("scope_ch1_waveform", 1.0)
    store.close()

    segments = read_closed_channel_segments(tmp_path / "channels", sent=set())
    assert len(segments) == 1
    assert segments[0].channel_id == "scope_ch1_waveform"


def test_read_closed_channel_segments_skips_already_sent(tmp_path: Path) -> None:
    sid = uuid4()
    store = ChannelStore(tmp_path, sid)
    store.write("psu.voltage", 3.3)
    store.close()

    channels_dir = tmp_path / "channels"
    first = read_closed_channel_segments(channels_dir, sent=set())
    assert len(first) == 1
    sent = {first[0].rel_path}

    again = read_closed_channel_segments(channels_dir, sent=sent)
    assert again == []


def test_read_closed_channel_segments_skips_a_still_open_segment(tmp_path: Path) -> None:
    """A segment that hasn't finished writing (no EOS marker yet) must be
    skipped -- not raised, not treated as sent -- so a later poll picks it up
    once the writer's single flush completes."""
    channels_dir = tmp_path / "channels" / _today()
    channels_dir.mkdir(parents=True)
    torn = channels_dir / "psu.voltage_deadbeef.arrow"
    # Deliberately truncated / non-IPC bytes -- simulates the brief window
    # mid-``_flush_pending`` before the writer closes the stream.
    torn.write_bytes(b"not a real arrow ipc stream")

    segments = read_closed_channel_segments(tmp_path / "channels", sent=set())
    assert segments == []


def test_read_closed_channel_segments_skips_empty_segment(tmp_path: Path) -> None:
    """A zero-row (but well-formed) segment is excluded -- nothing to forward."""
    schema = pa.schema([("received_at", pa.timestamp("us", tz="UTC")), ("value", pa.float64())])
    channels_dir = tmp_path / "channels" / _today()
    channels_dir.mkdir(parents=True)
    seg_path = channels_dir / "psu.voltage_00000000.arrow"
    empty = pa.table(
        {"received_at": pa.array([], type=schema.field(0).type), "value": []}, schema=schema
    )
    with pa.OSFile(str(seg_path), "wb") as sink, ipc.new_stream(sink, schema) as w:
        w.write_table(empty)

    segments = read_closed_channel_segments(tmp_path / "channels", sent=set())
    assert segments == []


def test_read_closed_channel_segments_multiple_channels_and_sessions(tmp_path: Path) -> None:
    s1, s2 = uuid4(), uuid4()
    store1 = ChannelStore(tmp_path, s1)
    store1.write("psu.voltage", 1.0)
    store1.close()
    store2 = ChannelStore(tmp_path, s2)
    store2.write("dmm.current", 2.0)
    store2.close()

    segments = read_closed_channel_segments(tmp_path / "channels", sent=set())
    assert {s.channel_id for s in segments} == {"psu.voltage", "dmm.current"}


# --------------------------------------------------------------------------- #
# File blobs                                                                   #
# --------------------------------------------------------------------------- #


def test_read_new_file_records_reads_a_real_blob_and_sidecar(tmp_path: Path) -> None:
    sid = str(uuid4())
    store = FileStore(_data_dir=tmp_path)
    uri = store.write("capture", b"hello world", session_id=sid, attributes={"note": "x"})

    records = read_new_file_records(tmp_path / "files", sent=set())
    assert len(records) == 1
    rec = records[0]
    assert rec.uri == uri
    assert rec.data == b"hello world"
    assert rec.session_id == sid
    assert rec.metadata.mime == "application/octet-stream"
    assert rec.metadata.attributes == {"note": "x"}


def test_read_new_file_records_skips_already_sent(tmp_path: Path) -> None:
    sid = str(uuid4())
    store = FileStore(_data_dir=tmp_path)
    uri = store.write("capture", b"hello world", session_id=sid)

    files_dir = tmp_path / "files"
    assert read_new_file_records(files_dir, sent={uri}) == []
    assert len(read_new_file_records(files_dir, sent=set())) == 1


def test_read_new_file_records_skips_sidecar_with_missing_blob(tmp_path: Path) -> None:
    sid = str(uuid4())
    store = FileStore(_data_dir=tmp_path)
    store.write("capture", b"hello world", session_id=sid)

    files_dir = tmp_path / "files"
    # Delete the blob but leave its sidecar -- a partial/racy state that must
    # not raise; the record is simply not forwardable (retried later if the
    # blob reappears, e.g. a slow remote-backend publish).
    for blob in files_dir.glob("*/*/capture.bin"):
        blob.unlink()

    assert read_new_file_records(files_dir, sent=set()) == []


def test_read_new_file_records_skips_unparseable_sidecar(tmp_path: Path) -> None:
    sid = str(uuid4())
    store = FileStore(_data_dir=tmp_path)
    store.write("capture", b"hello world", session_id=sid)

    files_dir = tmp_path / "files"
    for sidecar in files_dir.glob("*/*/capture.bin.meta.json"):
        sidecar.write_text("not json")

    assert read_new_file_records(files_dir, sent=set()) == []


def test_read_new_file_records_multiple_sessions(tmp_path: Path) -> None:
    store = FileStore(_data_dir=tmp_path)
    uri1 = store.write("a", b"1", session_id=str(uuid4()))
    uri2 = store.write("b", b"2", session_id=str(uuid4()))

    records = read_new_file_records(tmp_path / "files", sent=set())
    assert {r.uri for r in records} == {uri1, uri2}
