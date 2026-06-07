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

from litmus.data._duckdb_flight_server import DuckDBFlightServer

_SCHEMA = pa.schema([pa.field("x", pa.int64())])


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
