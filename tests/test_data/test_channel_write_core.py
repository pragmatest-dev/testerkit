"""The three write front-doors share one core, and the index log is runtime-readable.

``write`` (1 sample), ``write_many`` (N samples), and the ``stream`` sink all route
through ``ChannelStore._append_and_publish`` — so the same values produce identical
durable rows and offsets regardless of which verb wrote them. And a segment closed
*after* an index store opened is folded in at query time, so nothing is queryable
only-after-a-restart.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from litmus.data.channels.store import ChannelStore


def _values_and_offsets(store: ChannelStore, channel: str) -> tuple[list, list]:
    table = store.query(channel)
    return (
        table.column("value").to_pylist(),
        table.column("offset").to_pylist(),
    )


class TestWriteVerbAlignment:
    """write / write_many / stream → identical durable rows + offsets."""

    def test_three_front_doors_equivalent(self, tmp_path: Path):
        values = [3.3 + i * 0.01 for i in range(10)]

        # write() — one sample per call (N=1 case of the core)
        s_write = ChannelStore(tmp_path / "write", uuid4(), flush_threshold=4)
        s_write.open()
        for v in values:
            s_write.write("dmm.v", v, source="stream")
        s_write.close()

        # write_many() — one N-sample block
        s_many = ChannelStore(tmp_path / "many", uuid4(), flush_threshold=4)
        s_many.open()
        s_many.write_many("dmm.v", values, source="stream")
        s_many.close()

        # stream sink — buffers, flushes through write_many under the hood
        import litmus.channels as channels_mod
        from litmus.execution._state import set_channel_store

        s_stream = ChannelStore(tmp_path / "stream", uuid4(), flush_threshold=4)
        s_stream.open()
        set_channel_store(s_stream)
        try:
            with channels_mod.stream("dmm.v") as sink:
                for v in values:
                    sink.write(v)
        finally:
            set_channel_store(None)
        s_stream.close()

        v_write, o_write = _values_and_offsets(s_write, "dmm.v")
        v_many, o_many = _values_and_offsets(s_many, "dmm.v")
        v_stream, o_stream = _values_and_offsets(s_stream, "dmm.v")

        assert v_write == v_many == v_stream == values
        assert o_write == o_many == o_stream == list(range(10))

    def test_write_accumulates_into_segments_not_one_file_each(self, tmp_path: Path):
        # Per-sample writes must coalesce into segment-sized files, not explode
        # into one file per write (the batch-buffering contract).
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=100)
        store.open()
        for i in range(20):
            store.write("dmm.v", float(i))
        store.close()
        files = list((tmp_path / "channels").glob("*/*.arrow"))
        assert len(files) == 1  # 20 writes < threshold → one segment on close


class TestRuntimeReadableLog:
    """A segment closed after the index opened is queryable without a restart."""

    def test_runtime_fold_picks_up_new_segments(self, tmp_path: Path):
        # Producer writes a first block (flushes to a closed segment).
        producer = ChannelStore(tmp_path, uuid4(), flush_threshold=2)
        producer.open()
        producer.write_many("ch", [1.0, 2.0])  # >= threshold → closed segment

        # An index store opens and scans what's on disk so far.
        index = ChannelStore(tmp_path, uuid4(), index=True)
        index.open()
        assert index.query("ch").num_rows == 2

        # Producer writes MORE and closes — a new segment lands on disk.
        producer.write_many("ch", [3.0, 4.0])
        producer.close()

        # Without reopening the index, a query folds the new segment in.
        index._last_scan = 0.0  # bypass the scan throttle for the test
        result = index.query("ch")
        assert result.num_rows == 4
        assert sorted(float(v) for v in result.column("value").to_pylist()) == [
            1.0,
            2.0,
            3.0,
            4.0,
        ]
        index.close()

    def test_query_dedups_overlay_and_scanned_segment(self, tmp_path: Path):
        # A sample present in BOTH the live overlay and a scanned segment (same
        # session + offset) is counted once, not twice.
        from datetime import UTC, datetime

        from litmus.data.channels.models import ChannelSample, sample_to_batch

        # Producer writes one sample to a closed segment (offset 0).
        producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        producer.open()
        sid = str(producer.session_id)
        producer.write("ch", 7.0)
        producer.close()

        # Index store scans the segment → has the sample.
        index = ChannelStore(tmp_path, uuid4(), index=True)
        index.open()
        assert index.query("ch").num_rows == 1

        # Feed the SAME sample (session, offset 0) through the overlay, as the
        # relay would, then force a re-scan. The two copies collapse to one.
        s = ChannelSample(
            channel_id="ch",
            received_at=datetime.now(UTC),
            value=7.0,
            session_id=sid,
            offset=0,
        )
        index.ingest_batch("ch", sample_to_batch(s))
        index._last_scan = 0.0
        assert index.query("ch").num_rows == 1  # deduped, not 2
        index.close()
