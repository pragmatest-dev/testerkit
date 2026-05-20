"""Performance benchmarks for the data layer.

Measures throughput and latency at various scales so we can:
1. Track regressions over time (pytest-benchmark history)
2. Estimate where the local data system falls apart

Run with: pytest tests/test_data/test_perf.py -v --benchmark-only
Skip in CI: tests are marked @pytest.mark.benchmark
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.channels.store import ChannelStore
from litmus.data.event_store import EventStore
from litmus.data.events import MeasurementRecorded, SessionStarted

# ---------------------------------------------------------------------------
# Fixtures — ONE daemon per module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def event_store() -> Generator[EventStore]:
    """Module-scoped EventStore on the canonical singleton data_dir.

    Per-process isolation isn't needed for perf benchmarks — they
    measure throughput, not state. Per-test isolation is via unique
    ``session_id`` (each ``_make_measurement`` callsite mints its
    own). Pointing this at the canonical store prevents spawning a
    fresh events daemon (~100 gRPC threads) just for benchmarks.
    """
    s = EventStore()
    yield s
    s.close()


@pytest.fixture
def channel_store(tmp_path: Path) -> Generator[ChannelStore]:
    s = ChannelStore(tmp_path, uuid4())
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
        outcome="passed",
        limit_low=3.0,
        limit_high=3.6,
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

    # Query benchmarks below operate on sub-millisecond to low-millisecond
    # ranges. pytest-benchmark's defaults (5 rounds, no warmup, GC enabled)
    # produce mean/median variance >25% even on identical code, which
    # makes a release-time regression gate unusable. Override to:
    #   * warmup=True — let DuckDB caches / page-cache stabilize
    #   * min_rounds=30 — give the min statistic a stable floor
    #   * disable_gc=True — eliminate Python GC pauses from the sample
    # With these settings the release workflow compares `stats['min']`
    # (best-of-30 rounds) and a real >25% slowdown survives the gate
    # while CI scheduler jitter does not.

    @pytest.mark.benchmark(group="event-query", warmup=True, min_rounds=30, disable_gc=True)
    @pytest.mark.parametrize("n_events", [100, 1_000, 10_000])
    def test_query_scale(self, event_store: EventStore, benchmark, n_events: int):
        """Query performance as event count grows.

        Recorded for observability, but **excluded from the release
        regression gate** — every call routes through the events
        daemon (Flight RPC + DuckDB plan + OS page cache), and the
        floor swings 10× between local back-to-back runs of identical
        code. min-of-N can't stabilize against that. See
        ``GATE_EXCLUDE`` in ``.github/workflows/release.yml``.
        """
        sid = uuid4()
        for i in range(n_events):
            event_store.emit(_make_measurement(sid, i))

        def query_all():
            return event_store.events(session_id=sid)

        result = benchmark(query_all)
        assert len(result) == n_events

    @pytest.mark.benchmark(group="event-query", warmup=True, min_rounds=30, disable_gc=True)
    def test_query_by_type_10k(self, event_store: EventStore, benchmark):
        """Filter by event_type over 10k events."""
        sid = uuid4()
        event_store.emit(
            SessionStarted(
                session_id=sid,
                station_id="bench",
                session_type="test",
                pid=os.getpid(),
            )
        )
        for i in range(10_000):
            event_store.emit(_make_measurement(sid, i))

        def query_type():
            return event_store.events(session_id=sid, event_type="session.started")

        result = benchmark(query_type)
        assert len(result) == 1

    @pytest.mark.benchmark(group="event-query", warmup=True, min_rounds=30, disable_gc=True)
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
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=100)
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
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=100)
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
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=100)
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

        store = ChannelStore(tmp_path, uuid4(), flush_threshold=50)
        store.open()
        waveform = [random.gauss(0, 1) for _ in range(1000)]

        def write_arrays():
            for _ in range(1_000):
                store.write("scope.ch1", waveform, sample_interval=1e-6)

        benchmark(write_arrays)
        store.close()
