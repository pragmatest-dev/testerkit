"""In-process tests for DuckDBFlightServer push subscriptions (Phase 1).

The unified server gains a live push path: a ``do_get`` with the
``__SUBSCRIBE__`` ticket opens a held-open stream, and every ``do_put``
batch is fanned out to subscribers with no polling. These tests run an
in-process server (server thread + Flight client in the same process) —
no subprocess daemon, no canonical data dir — so they're fast and can't
exhaust the box.
"""

from __future__ import annotations

import threading

import duckdb
import pyarrow as pa
import pyarrow.flight as flight
import pytest

from litmus.data._duckdb_flight_server import (
    DuckDBFlightServer,
    _apply_filter,
    _parse_subscribe,
    _SubscriberBuffer,
)

_SCHEMA = pa.schema([pa.field("x", pa.int64())])


def _rb(i: int) -> pa.RecordBatch:
    return pa.record_batch({"x": pa.array([i], type=pa.int64())})


def test_buffer_lossy_drops_oldest_and_counts_gaps() -> None:
    """No replay_sql → lossy: overflow drops the oldest + counts a gap, never
    signals removal; the subscriber stays attached."""
    buf = _SubscriberBuffer(lossy=True, maxsize=2)
    assert buf.put(_rb(1)) is True
    assert buf.put(_rb(2)) is True
    assert buf.put(_rb(3)) is True  # overflow: drop oldest, keep subscriber
    assert buf.gaps == 1
    drained = buf.drain(0.1)
    assert drained is not None
    assert [b.column("x").to_pylist()[0] for b in drained] == [2, 3]


def test_buffer_lossless_signals_removal_on_overflow() -> None:
    """replay_sql-backed → lossless: overflow returns False so _publish drops
    the subscriber (→ client reconnects + replays). No oldest-drop, no gap."""
    buf = _SubscriberBuffer(lossy=False, maxsize=2)
    assert buf.put(_rb(1)) is True
    assert buf.put(_rb(2)) is True
    assert buf.put(_rb(3)) is False  # overflow → remove subscriber
    assert buf.gaps == 0


def test_buffer_drain_coalesces_all_queued() -> None:
    """One drain returns every queued batch (LMAX catch-up)."""
    buf = _SubscriberBuffer(lossy=True, maxsize=100)
    for i in range(5):
        buf.put(_rb(i))
    drained = buf.drain(0.1)
    assert drained is not None
    assert len(drained) == 5


def test_buffer_drain_returns_none_when_closed() -> None:
    buf = _SubscriberBuffer(lossy=True, maxsize=10)
    buf.close()
    assert buf.drain(0.1) is None


def test_buffer_conflate_keeps_only_newest() -> None:
    """LATEST gauge: each put conflates to the newest batch, no gap count."""
    buf = _SubscriberBuffer(lossy=True, maxsize=100, conflate=True)
    for i in range(5):
        buf.put(_rb(i))
    drained = buf.drain(0.1)
    assert drained is not None
    assert [b.column("x").to_pylist()[0] for b in drained] == [4]  # only newest
    assert buf.gaps == 0  # conflation is intentional, not overflow


def _make_server(*, with_subscribe: bool = True) -> tuple[DuckDBFlightServer, str]:
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE t (x BIGINT)")
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("testdb", conn)
    if with_subscribe:
        server.register_subscribe_schema("testdb", _SCHEMA)
    location = f"grpc://127.0.0.1:{server.port}"
    threading.Thread(target=server.serve, daemon=True, name="test-flight").start()
    return server, location


def _put(location: str, values: list[int]) -> None:
    client = flight.connect(location)
    batch = pa.record_batch({"x": pa.array(values, type=pa.int64())})
    descriptor = flight.FlightDescriptor.for_command(b"testdb\0t")
    writer, reader = client.do_put(descriptor, batch.schema)
    writer.write_batch(batch)
    # Drain the per-batch ack so the insert is confirmed before we return.
    reader.read()
    writer.close()
    client.close()


def test_query_path_still_works() -> None:
    """The one-shot SQL ticket is unaffected by the new SUB branch."""
    server, location = _make_server()
    try:
        _put(location, [1, 2, 3])
        client = flight.connect(location)
        result = client.do_get(flight.Ticket(b"testdb\0SELECT * FROM t ORDER BY x")).read_all()
        assert result.column("x").to_pylist() == [1, 2, 3]
        client.close()
    finally:
        server.shutdown()


def test_subscribe_receives_pushed_rows() -> None:
    """A held-open SUB stream receives do_put batches with no polling."""
    server, location = _make_server()
    received: list[pa.RecordBatch] = []
    registered = threading.Event()
    try:
        sub_client = flight.connect(location)

        def _subscribe() -> None:
            reader = sub_client.do_get(flight.Ticket(b"testdb\0__SUBSCRIBE__"))
            # do_get returns only after the server-side _do_subscribe has
            # registered the queue, so the subsequent put is guaranteed
            # to see this subscriber.
            registered.set()
            for chunk in reader:
                received.append(chunk.data)
                break

        t = threading.Thread(target=_subscribe, daemon=True)
        t.start()
        assert registered.wait(timeout=5), "subscription never registered"

        _put(location, [7])
        t.join(timeout=5)

        assert received, "subscriber received no pushed batch"
        assert received[0].column("x").to_pylist() == [7]
    finally:
        server.shutdown()


def test_subscribe_rejected_without_schema() -> None:
    """A db that didn't register a subscribe schema rejects SUB tickets."""
    server, location = _make_server(with_subscribe=False)
    try:
        client = flight.connect(location)
        with pytest.raises(flight.FlightError):
            client.do_get(flight.Ticket(b"testdb\0__SUBSCRIBE__")).read_all()
        client.close()
    finally:
        server.shutdown()


def _ctbl(cids: list[str]) -> pa.Table:
    return pa.table(
        {
            "cid": pa.array(cids, type=pa.string()),
            "v": pa.array(range(len(cids)), type=pa.int64()),
        }
    )


def test_parse_subscribe() -> None:
    # (cursor, conflate, predicates)
    assert _parse_subscribe("") == (0, False, {})
    assert _parse_subscribe("channel_id=dmm.voltage") == (0, False, {"channel_id": "dmm.voltage"})
    assert _parse_subscribe("cursor=42") == (42, False, {})
    assert _parse_subscribe("conflate=latest&channel_id=x") == (0, True, {"channel_id": "x"})
    # cursor + conflate are reserved control keys, not filter predicates
    assert _parse_subscribe("cursor=5&conflate=latest&event_type=run.ended") == (
        5,
        True,
        {"event_type": "run.ended"},
    )


def test_apply_filter_empty_returns_table_unchanged() -> None:
    t = _ctbl(["a", "b"])
    assert _apply_filter(t, {}) is t  # no-filter path: no copy


def test_apply_filter_all_match_returns_same_table() -> None:
    t = _ctbl(["a", "a"])
    assert _apply_filter(t, {"cid": "a"}) is t  # whole-batch keep, no copy


def test_apply_filter_keeps_only_matching_rows() -> None:
    out = _apply_filter(_ctbl(["a", "b", "a"]), {"cid": "a"})
    assert out is not None
    assert out.column("cid").to_pylist() == ["a", "a"]


def test_apply_filter_no_match_returns_none() -> None:
    assert _apply_filter(_ctbl(["a", "b"]), {"cid": "z"}) is None


def test_apply_filter_absent_column_returns_none() -> None:
    assert _apply_filter(_ctbl(["a"]), {"nope": "x"}) is None


def _put_m(location: str, cid: str, v: int) -> None:
    client = flight.connect(location)
    batch = pa.record_batch(
        {"cid": pa.array([cid], type=pa.string()), "v": pa.array([v], type=pa.int64())}
    )
    writer, reader = client.do_put(flight.FlightDescriptor.for_command(b"mdb\0m"), batch.schema)
    writer.write_batch(batch)
    reader.read()
    writer.close()
    client.close()


def test_subscribe_applies_server_side_filter() -> None:
    """A SUB ticket carrying a filter delivers only matching rows — a
    non-matching push is dropped server-side (channels' per-channel_id routing,
    no client noise)."""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE m (cid VARCHAR, v BIGINT)")
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("mdb", conn)
    server.register_subscribe_schema("mdb", pa.schema([("cid", pa.string()), ("v", pa.int64())]))
    location = f"grpc://127.0.0.1:{server.port}"
    threading.Thread(target=server.serve, daemon=True, name="test-flight-f").start()

    received: list[pa.RecordBatch] = []
    registered = threading.Event()
    try:
        sub_client = flight.connect(location)

        def _subscribe() -> None:
            # filter cid=a → only "a" rows. Ticket: db\0__SUBSCRIBE__\0<querystring>
            reader = sub_client.do_get(flight.Ticket(b"mdb\0__SUBSCRIBE__\0cid=a"))
            registered.set()
            for chunk in reader:
                received.append(chunk.data)
                break

        t = threading.Thread(target=_subscribe, daemon=True)
        t.start()
        assert registered.wait(timeout=5), "subscription never registered"

        _put_m(location, "b", 1)  # filtered out server-side
        _put_m(location, "a", 2)  # delivered
        t.join(timeout=5)

        assert received, "filtered subscriber received nothing"
        assert received[0].column("cid").to_pylist() == ["a"]
        assert received[0].column("v").to_pylist() == [2]
    finally:
        server.shutdown()


def test_query_hook_serves_do_get_without_a_duckdb_conn() -> None:
    """A db with a registered query hook serves do_get via the hook — the
    payload is parsed by the hook (a typed verb, not SQL) and the db needs no
    DuckDB connection. The seam channels' query path plugs into (Phase 5)."""
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    seen: list[str] = []

    def _hook(payload: str) -> pa.Table:
        seen.append(payload)
        return pa.table({"n": pa.array([len(payload)], type=pa.int64())})

    server.register_query_hook("verbdb", _hook)  # note: no server.register(conn)
    location = f"grpc://127.0.0.1:{server.port}"
    threading.Thread(target=server.serve, daemon=True, name="test-flight-qh").start()
    try:
        client = flight.connect(location)
        table = client.do_get(flight.Ticket(b"verbdb\0dmm.voltage?last_n=10")).read_all()
        client.close()
        assert seen == ["dmm.voltage?last_n=10"]
        assert table.column("n").to_pylist() == [len("dmm.voltage?last_n=10")]
    finally:
        server.shutdown()
