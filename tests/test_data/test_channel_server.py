"""Tests for Arrow Flight channel server/client integration.

The Flight server runs as a daemon process (spawned by flight_manager).
Tests exercise:
- Daemon lifecycle (acquire/release/ref counting)
- Remote write via ChannelClient → daemon persists
- Remote subscribe via ChannelClient → live samples
- Historical query via ChannelClient
- In-process server (start_server_background) for unit-level tests

Daemon-spawning tests (``TestDaemonLifecycle``) point at the
canonical channels dir so they all share **one** daemon for the
whole test file — spawning a fresh daemon per test (~100 gRPC
threads each) hits WSL's pids cgroup at ~30 daemons. In-process
server tests (``TestInProcessServer``) keep using ``tmp_path``
because the in-process FlightServerBase shuts down at end of
test; no detached daemon survives.
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from litmus.data.channels.client import ChannelClient
from litmus.data.channels.models import ChannelSample
from litmus.data.channels.server import start_server_background
from litmus.data.channels.store import ChannelStore
from litmus.data.data_dir import resolve_data_dir

# Project-local results via repo ``litmus.yaml`` — daemon-spawning
# tests share this so we get exactly one channels daemon for the
# whole TestDaemonLifecycle class.
_CANONICAL_RESULTS = resolve_data_dir()


def _make_store(data_dir: Path, *, serve: bool = False) -> ChannelStore:
    # ``index = not serve``: a serve=False store here is wrapped in an
    # in-process server (it plays the daemon → owns the warm index);
    # a serve=True store is a producer connecting to the real daemon
    # (which owns the index), so it must NOT build its own (Opt 1).
    store = ChannelStore(
        data_dir,
        uuid4(),
        flush_threshold=10,
        serve=serve,
        index=not serve,
    )
    store.open()
    return store


# ---------------------------------------------------------------------------
# In-process server tests (direct, no daemon)
# ---------------------------------------------------------------------------


class TestInProcessServer:
    """Test Flight server/client directly (no daemon) for fast unit tests."""

    def test_server_starts_and_stops(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        server, location = start_server_background(store)
        assert location.startswith("grpc://")
        server.shutdown()
        store.close()

    def test_list_flights(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write("dmm.voltage", 3.3)
        store.write("psu.current", 0.5)
        server, location = start_server_background(store)

        client = ChannelClient(location)
        channels = client.channels()
        assert len(channels) == 2
        ids = {c.channel_id for c in channels}
        assert ids == {"dmm.voltage", "psu.current"}

        client.close()
        server.shutdown()
        store.close()

    def test_channels_carry_full_descriptor(self, tmp_path: Path) -> None:
        """list_flights serves the full descriptor (units/role) via app_metadata."""
        store = _make_store(tmp_path)
        store.write("dmm.voltage", 3.3, units="V", instrument_role="dmm")
        server, location = start_server_background(store)

        client = ChannelClient(location)
        (desc,) = client.channels()
        assert desc.channel_id == "dmm.voltage"
        assert desc.units == "V"
        assert desc.instrument_role == "dmm"
        assert desc.data_type == "scalar:float"

        client.close()
        server.shutdown()
        store.close()

    def test_registry_verb_round_trip(self, tmp_path: Path) -> None:
        # A producer writes a closed segment; an index store scans it and serves
        # the (hostname, channel, session) registry over the __registry__ verb.
        prod = ChannelStore(tmp_path, uuid4(), flush_threshold=1, station_hostname="h1")
        prod.open()
        prod.write("dmm.voltage", 3.3)
        prod.close()

        store = ChannelStore(tmp_path, uuid4(), index=True)
        store.open()
        server, location = start_server_background(store)

        client = ChannelClient(location)
        rows = client.channel_registry().to_pylist()
        client.close()
        server.shutdown()
        store.close()

        assert len(rows) == 1
        assert rows[0]["hostname"] == "h1"
        assert rows[0]["channel_id"] == "dmm.voltage"
        assert rows[0]["session_id"]

    def test_remote_write_persists(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        server, location = start_server_background(store)

        client = ChannelClient(location)
        client.write("remote.temp", 22.5)
        time.sleep(0.1)

        result = store.query("remote.temp")
        assert len(result) == 1

        client.close()
        server.shutdown()
        store.close()

    def test_remote_write_notifies_in_process(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        server, location = start_server_background(store)
        received: list[ChannelSample] = []
        store.on_channel("remote.ch", received.append)

        client = ChannelClient(location)
        client.write("remote.ch", 42.0)
        time.sleep(0.1)

        assert len(received) == 1
        assert received[0].value == 42.0

        client.close()
        server.shutdown()
        store.close()

    def test_client_receives_live_samples(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        server, location = start_server_background(store)
        received: list[ChannelSample] = []

        client = ChannelClient(location)
        unsub = client.on_channel("sensor.temp", received.append)
        time.sleep(0.2)

        store.write("sensor.temp", 25.0)
        store.write("sensor.temp", 26.0)
        time.sleep(0.3)

        assert len(received) == 2
        assert received[0].value == 25.0
        assert received[1].value == 26.0

        unsub()
        client.close()
        server.shutdown()
        store.close()

    def test_multiple_subscribers(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        server, location = start_server_background(store)
        received_a: list[ChannelSample] = []
        received_b: list[ChannelSample] = []

        client_a = ChannelClient(location)
        client_b = ChannelClient(location)
        client_a.on_channel("ch.x", received_a.append)
        client_b.on_channel("ch.x", received_b.append)
        time.sleep(0.2)

        store.write("ch.x", 1.0)
        time.sleep(0.3)

        assert len(received_a) == 1
        assert len(received_b) == 1

        client_a.close()
        client_b.close()
        server.shutdown()
        store.close()

    def test_historical_query(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        for i in range(10):
            store.write("hist.ch", float(i))
        server, location = start_server_background(store)

        client = ChannelClient(location)
        table = client.query("hist.ch")
        assert len(table) == 10

        table_limited = client.query("hist.ch", last_n=3)
        assert len(table_limited) == 3

        client.close()
        server.shutdown()
        store.close()

    def test_query_with_time_window(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        for i in range(10):
            store.write("ts.ch", float(i))
        server, location = start_server_background(store)

        client = ChannelClient(location)
        table = client.query("ts.ch", max_points=5)
        assert len(table) == 5

        all_ts = store.query("ts.ch").column("received_at").to_pylist()
        mid = all_ts[5]
        table_windowed = client.query("ts.ch", start=mid)
        assert len(table_windowed) == 5

        table_zoom = client.query("ts.ch", start=mid, max_points=3)
        assert len(table_zoom) == 3

        client.close()
        server.shutdown()
        store.close()


# ---------------------------------------------------------------------------
# Daemon lifecycle tests
# ---------------------------------------------------------------------------


class TestIndexNoDrift:
    """The warm index must read the same whether a sample arrived live
    (do_put → pending buffer) or was scanned from a closed segment on a
    fresh daemon start. A live row and a disk-scanned row of the same
    data must query identically — the channels analog of Phase B's
    inflight==materialized no-drift guard.
    """

    def test_live_ingest_matches_disk_scan(self, tmp_path: Path) -> None:
        # Store A indexes via the write→index hook (live/pending path)
        # and persists segments to disk.
        store_a = ChannelStore(tmp_path, uuid4(), flush_threshold=10, index=True)
        store_a.open()
        store_a.write("nd.scalar", 1.5)
        store_a.write("nd.scalar", 2.5)
        store_a.write("nd.arr", [1.0, 2.0, 3.0])
        live_scalar = store_a.query("nd.scalar").column("value").to_pylist()
        live_arr = store_a.query("nd.arr").column("value").to_pylist()
        store_a.close()  # flush remaining segments to disk

        # Store B rebuilds the index purely from the disk scan.
        store_b = ChannelStore(tmp_path, uuid4(), index=True)
        store_b.open()
        disk_scalar = store_b.query("nd.scalar").column("value").to_pylist()
        disk_arr = store_b.query("nd.arr").column("value").to_pylist()
        store_b.close()

        # Live path and disk-scan path agree, and types round-trip.
        assert live_scalar == disk_scalar == [1.5, 2.5]
        assert live_arr == disk_arr == [[1.0, 2.0, 3.0]]


class TestDaemonLifecycle:
    """Test the flight_manager acquire/release and daemon spawning.

    All four tests share **one** canonical channels daemon —
    spawned on the first test, reused thereafter (idle timeout
    keeps it alive across the file). Per-test isolation is by
    unique ``session_id`` (the ``uuid4()`` in ``_make_store``)
    and unique channel names.
    """

    def test_acquire_starts_daemon(self) -> None:
        store = _make_store(_CANONICAL_RESULTS, serve=True)
        assert store.flight_location is not None
        assert store.flight_location.startswith("grpc://")
        assert (_CANONICAL_RESULTS / "channels" / "_flight.json").exists()
        store.close()

    def test_second_acquire_reuses_daemon(self) -> None:
        store_a = _make_store(_CANONICAL_RESULTS, serve=True)
        loc_a = store_a.flight_location

        store_b = ChannelStore(
            _CANONICAL_RESULTS,
            uuid4(),
            flush_threshold=10,
            serve=True,
        )
        store_b.open()
        loc_b = store_b.flight_location

        assert loc_a == loc_b  # Same daemon, same port

        store_a.close()
        store_b.close()

    def test_client_can_connect_to_daemon(self) -> None:
        store = _make_store(_CANONICAL_RESULTS, serve=True)
        assert store.flight_location is not None

        client = ChannelClient(store.flight_location)
        channels = client.channels()  # Should not raise
        assert isinstance(channels, list)

        client.close()
        store.close()

    def test_write_via_daemon(self) -> None:
        """Write via ChannelClient → daemon persists → queryable."""
        store = _make_store(_CANONICAL_RESULTS, serve=True)
        assert store.flight_location is not None

        # Unique channel name so we don't read another test's writes
        # from the shared canonical store.
        ch = f"remote.val.{uuid4().hex[:8]}"

        client = ChannelClient(store.flight_location)
        client.write(ch, 99.0)
        time.sleep(0.2)

        # Query via client (reads from daemon's store)
        table = client.query(ch)
        assert len(table) == 1

        client.close()
        store.close()
