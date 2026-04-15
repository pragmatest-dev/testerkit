"""Tests for Arrow Flight channel server/client integration.

The Flight server runs as a daemon process (spawned by flight_manager).
Tests exercise:
- Daemon lifecycle (acquire/release/ref counting)
- Remote write via ChannelClient → daemon persists
- Remote subscribe via ChannelClient → live samples
- Historical query via ChannelClient
- In-process server (start_server_background) for unit-level tests
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from litmus.data.channels.client import ChannelClient
from litmus.data.channels.models import ChannelSample
from litmus.data.channels.server import start_server_background
from litmus.data.channels.store import ChannelStore


def _make_store(tmp_path: Path, *, serve: bool = False) -> ChannelStore:
    store = ChannelStore(
        tmp_path / "channels",
        uuid4(),
        flush_threshold=10,
        serve=serve,
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

        all_ts = store.query("ts.ch").column("timestamp").to_pylist()
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


class TestDaemonLifecycle:
    """Test the flight_manager acquire/release and daemon spawning."""

    def test_acquire_starts_daemon(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, serve=True)
        assert store.flight_location is not None
        assert store.flight_location.startswith("grpc://")
        assert (tmp_path / "channels" / "_flight.json").exists()
        store.close()

    def test_second_acquire_reuses_daemon(self, tmp_path: Path) -> None:
        store_a = _make_store(tmp_path, serve=True)
        loc_a = store_a.flight_location

        store_b = ChannelStore(
            tmp_path / "channels",
            uuid4(),
            flush_threshold=10,
            serve=True,
        )
        store_b.open()
        loc_b = store_b.flight_location

        assert loc_a == loc_b  # Same daemon, same port

        store_a.close()
        store_b.close()

    def test_client_can_connect_to_daemon(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, serve=True)
        assert store.flight_location is not None

        client = ChannelClient(store.flight_location)
        channels = client.channels()  # Should not raise
        assert isinstance(channels, list)

        client.close()
        store.close()

    def test_write_via_daemon(self, tmp_path: Path) -> None:
        """Write via ChannelClient → daemon persists → queryable."""
        store = _make_store(tmp_path, serve=True)
        assert store.flight_location is not None

        client = ChannelClient(store.flight_location)
        client.write("remote.val", 99.0)
        time.sleep(0.2)

        # Query via client (reads from daemon's store)
        table = client.query("remote.val")
        assert len(table) == 1

        client.close()
        store.close()
