"""Performance benchmarks for the data layer.

Measures throughput and latency at various scales so we can:
1. Track regressions over time (pytest-benchmark history)
2. Estimate where the local data system falls apart

Run with: pytest tests/test_data/test_perf.py -v --benchmark-only
Skip in CI: tests are marked @pytest.mark.benchmark
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pytest

from litmus.data.channels.store import ChannelStore
from litmus.data.event_store import EventStore
from litmus.data.events import MeasurementRecorded, SessionStarted

# ---------------------------------------------------------------------------
# Fixtures — ONE daemon per module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def event_store(tmp_path_factory: pytest.TempPathFactory) -> Generator[EventStore]:
    d = tmp_path_factory.mktemp("perf_events")
    s = EventStore(_results_dir=d / "results")
    yield s
    s.close()


@pytest.fixture
def channel_store(tmp_path: Path) -> Generator[ChannelStore]:
    s = ChannelStore(tmp_path / "channels", uuid4())
    s.open()
    yield s
    s.close()


def _make_measurement(session_id, i: int) -> MeasurementRecorded:
    return MeasurementRecorded(
        session_id=session_id,
        step_name=f"step_{i % 10}",
        step_index=i % 10,
        measurement_name=f"voltage_{i}",
        value=3.3 + (i % 100) * 0.01,
        units="V",
        outcome="pass",
        low_limit=3.0,
        high_limit=3.6,
    )


# ---------------------------------------------------------------------------
# EventStore benchmarks
# ---------------------------------------------------------------------------

class TestEventStorePerf:
    """Benchmark emit and query at various scales."""

    @pytest.mark.benchmark(group="event-emit")
    def test_emit_1k(self, event_store: EventStore, benchmark):
        sid = uuid4()
        events = [_make_measurement(sid, i) for i in range(1000)]

        def emit_all():
            for e in events:
                event_store.emit(e)

        benchmark(emit_all)

    @pytest.mark.benchmark(group="event-query")
    @pytest.mark.parametrize("n_events", [100, 1_000, 10_000])
    def test_query_scale(self, event_store: EventStore, benchmark, n_events: int):
        """Query performance as event count grows."""
        sid = uuid4()
        for i in range(n_events):
            event_store.emit(_make_measurement(sid, i))

        def query_all():
            return event_store.events(session_id=sid)

        result = benchmark(query_all)
        assert len(result) == n_events

    @pytest.mark.benchmark(group="event-query")
    def test_query_by_type_10k(self, event_store: EventStore, benchmark):
        """Filter by event_type over 10k events."""
        sid = uuid4()
        event_store.emit(SessionStarted(
            session_id=sid, station_id="bench",
            session_type="test", pid=1,
        ))
        for i in range(10_000):
            event_store.emit(_make_measurement(sid, i))

        def query_type():
            return event_store.events(session_id=sid, event_type="session.started")

        result = benchmark(query_type)
        assert len(result) == 1

    @pytest.mark.benchmark(group="event-query")
    def test_query_multi_session(self, event_store: EventStore, benchmark):
        """Query across 50 sessions, 200 events each (10k total)."""
        target_sid = None
        for s in range(50):
            sid = uuid4()
            if s == 25:
                target_sid = sid
            for i in range(200):
                event_store.emit(_make_measurement(sid, i))

        def query_one_session():
            return event_store.events(session_id=target_sid)

        result = benchmark(query_one_session)
        assert len(result) == 200


# ---------------------------------------------------------------------------
# ChannelStore benchmarks
# ---------------------------------------------------------------------------

class TestChannelStorePerf:
    """Benchmark write and query for channel data."""

    @pytest.mark.benchmark(group="channel-write")
    @pytest.mark.parametrize("n_samples", [100, 1_000, 10_000])
    def test_write_scalars(self, tmp_path: Path, benchmark, n_samples: int):
        """Write scalar channel data at various scales."""
        store = ChannelStore(tmp_path / "ch", uuid4(), flush_threshold=100)
        store.open()

        def write_all():
            for i in range(n_samples):
                store.write("sensor.temp", 25.0 + i * 0.01, units="°C")

        benchmark(write_all)
        store.close()

    @pytest.mark.benchmark(group="channel-query")
    @pytest.mark.parametrize("n_samples", [1_000, 10_000])
    def test_query_scalars(self, tmp_path: Path, benchmark, n_samples: int):
        """Query channel data at various scales."""
        store = ChannelStore(tmp_path / "ch", uuid4(), flush_threshold=100)
        store.open()
        for i in range(n_samples):
            store.write("sensor.temp", 25.0 + i * 0.01, units="°C")

        def query():
            return store.query("sensor.temp")

        result = benchmark(query)
        assert len(result) == n_samples
        store.close()

    @pytest.mark.benchmark(group="channel-query")
    def test_query_with_lttb(self, tmp_path: Path, benchmark):
        """Query 10k samples decimated to 500 via LTTB."""
        store = ChannelStore(tmp_path / "ch", uuid4(), flush_threshold=100)
        store.open()
        for i in range(10_000):
            store.write("sensor.temp", 25.0 + i * 0.01, units="°C")

        def query_decimated():
            return store.query("sensor.temp", max_points=500)

        result = benchmark(query_decimated)
        assert len(result) <= 500
        store.close()

    @pytest.mark.benchmark(group="channel-write")
    def test_write_array_channel(self, tmp_path: Path, benchmark):
        """Write array (waveform-like) data — 1k writes of 1k-sample arrays."""
        import random
        store = ChannelStore(tmp_path / "ch", uuid4(), flush_threshold=50)
        store.open()
        waveform = [random.gauss(0, 1) for _ in range(1000)]

        def write_arrays():
            for _ in range(1_000):
                store.write("scope.ch1", waveform, sample_interval=1e-6)

        benchmark(write_arrays)
        store.close()


# ---------------------------------------------------------------------------
# Parquet _enforce_schema benchmark
# ---------------------------------------------------------------------------

class TestEnforceSchemaPerf:
    """Benchmark Arrow-native type coercion."""

    @pytest.mark.benchmark(group="parquet-schema")
    @pytest.mark.parametrize("n_rows", [100, 1_000, 10_000])
    def test_enforce_schema(self, benchmark, n_rows: int):
        from litmus.data.schemas import _enforce_schema

        # Build a table with string timestamps (the hard coercion case)
        data = {
            "run_id": [f"run-{i}" for i in range(n_rows)],
            "run_started_at": ["2026-03-08T12:00:00+00:00"] * n_rows,
            "run_ended_at": ["2026-03-08T12:01:00+00:00"] * n_rows,
            "step_name": [f"step_{i % 5}" for i in range(n_rows)],
            "step_index": list(range(n_rows)),
            "measurement_name": [f"meas_{i}" for i in range(n_rows)],
            "value": [3.3 + i * 0.001 for i in range(n_rows)],
            "dut_serial": ["SN001"] * n_rows,
            "station_id": ["station-1"] * n_rows,
        }
        table = pa.table(data)

        result = benchmark(_enforce_schema, table)
        assert len(result) == n_rows
