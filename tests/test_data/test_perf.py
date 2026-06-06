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

        Small-count variants (100, 1000) are excluded from the release
        regression gate — every call routes through the events daemon
        (Flight RPC + DuckDB plan + OS page cache), and the ~1–2 ms
        irreducible noise floor swamps a 10%-min gate on a sub-30 ms
        base. The 10k variant has a ~220 ms base (10% = 22 ms, well
        above the noise floor) and stays gated. See ``GATE_EXCLUDE``
        in ``.github/workflows/release.yml``.
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
# EventStore payload-field filter benchmarks (item 19 — item 21 baseline)
#
# These pin the cost of today's two-stage payload filter:
#   1. SQL pulls every event matching the envelope filter (fast — typed
#      columns, DuckDB columnar pushdown).
#   2. Python parses the ``json`` column row-by-row and filters on a
#      payload field (slow — JSON parse per row).
#
# Item 21 (typed Arrow event payloads) turns step 2 into native columnar
# access + SQL pushdown — design doc §2 estimates 10–50× on the read
# side. These benchmarks are the baseline that item 21 will be measured
# against; the post-item-21 perf-bench compares stats['min'] against
# these numbers and fails if the gain is less than the floor.
# ---------------------------------------------------------------------------


class TestEventStorePayloadFilterPerf:
    """Pin the cost of the payload-field filter path (the item 21 target)."""

    @pytest.mark.benchmark(group="event-payload-parse", warmup=True, min_rounds=30, disable_gc=True)
    def test_parse_payload_cost_10k(self, event_store: EventStore, benchmark):
        """Isolated JSON-parse cost — call ``_parse_event_row`` per row.

        Subtracts the SQL/Flight transport from the total payload-filter
        cost so item 21's payload-side win is measurable in isolation.
        Post-item-21: this should collapse to near-zero (typed Arrow
        access — no per-row Python JSON parse).
        """
        from litmus.data.event_store import _parse_event_row

        sid = uuid4()
        for i in range(10_000):
            event_store.emit(_make_measurement(sid, i))
        # Flush buffered events to Flight before the raw pull —
        # event_store.events() does this internally; the raw
        # _flight_query() does not.
        event_store.flush()
        rows = event_store._flight_query(
            f"SELECT * FROM events WHERE session_id = '{sid}' ORDER BY received_at"
        )
        assert len(rows) == 10_000

        def parse_all():
            return [_parse_event_row(row) for row in rows]

        result = benchmark(parse_all)
        assert len(result) == 10_000
        # Every event has the payload field we're checking — sanity guard
        # against a future schema rename that would silently make the
        # benchmark measure the empty path.
        assert all(e.get("measurement_name", "").startswith("voltage_") for e in result)

    @pytest.mark.benchmark(
        group="event-payload-filter", warmup=True, min_rounds=30, disable_gc=True
    )
    def test_query_by_payload_field_outcome_10k(self, event_store: EventStore, benchmark):
        """Full payload-filter path: query 10k events then keep ``outcome="failed"``.

        Mirrors what every cross-process subscriber does today via
        ``_Subscription.matches`` — Python post-filter on a payload
        field that SQL can't pushdown. Post-item-21 the outcome field
        becomes a typed Arrow column; this query collapses to envelope
        speed (10–50× per design doc §2).
        """
        sid = uuid4()
        # Mixed outcomes — 1 in 3 failed, so the filter has work to do.
        for i in range(10_000):
            outcome = "failed" if i % 3 == 0 else "passed"
            evt = _make_measurement(sid, i)
            evt.outcome = outcome
            event_store.emit(evt)

        def query_then_filter_failed():
            all_events = event_store.events(session_id=sid, event_type="test.measurement")
            return [e for e in all_events if e.get("outcome") == "failed"]

        result = benchmark(query_then_filter_failed)
        assert 3_000 <= len(result) <= 3_500  # ~1/3 of 10k

    @pytest.mark.benchmark(
        group="event-payload-filter", warmup=True, min_rounds=30, disable_gc=True
    )
    def test_query_by_role_10k(self, event_store: EventStore, benchmark):
        """The existing ``role=`` filter — calls into ``events()``
        which runs the role post-filter (event_matches_role) on every
        row after the SQL+JSON round-trip. Same shape as the
        outcome-filter benchmark above; pins the dual via the public
        API path.
        """
        sid = uuid4()
        from litmus.data.events import InstrumentConnected

        # 10k mixed events; ~1/4 have the role we'll filter for
        for i in range(10_000):
            if i % 4 == 0:
                event_store.emit(
                    InstrumentConnected(
                        session_id=sid,
                        role="dmm",
                        instrument_id=f"dmm_{i}",
                        resource="GPIB::16",
                    )
                )
            else:
                event_store.emit(_make_measurement(sid, i))

        def query_by_role():
            return event_store.events(session_id=sid, role="dmm")

        result = benchmark(query_by_role)
        assert 2_400 <= len(result) <= 2_600


# ---------------------------------------------------------------------------
# EventStore typed-payload-column pushdown benchmarks (item 21)
#
# These pin the cost of the SAME queries above, now that the payload
# fields they filter on are promoted to typed DuckDB columns.
# Compare side-by-side with :class:`TestEventStorePayloadFilterPerf`
# to read off the item-21 speedup.
# ---------------------------------------------------------------------------


class TestEventStoreTypedColumnPushdownPerf:
    """Pin the cost of typed-column pushdown vs item-19 baselines."""

    @pytest.mark.benchmark(
        group="event-payload-filter", warmup=True, min_rounds=30, disable_gc=True
    )
    def test_pushdown_outcome_failed_10k(self, event_store: EventStore, benchmark):
        """Pushdown variant of ``test_query_by_payload_field_outcome_10k``.

        The ``outcome`` field is now a typed DuckDB column populated
        from each event's ``typed_payload_values()``. The filter
        ``outcome = 'failed'`` planned directly against the column
        (no per-row Python JSON parse).
        """
        sid = uuid4()
        for i in range(10_000):
            outcome = "failed" if i % 3 == 0 else "passed"
            evt = _make_measurement(sid, i)
            evt.outcome = outcome
            event_store.emit(evt)

        def query_pushdown_failed():
            # SQL pushdown via direct daemon query — the public
            # ``events()`` API only exposes whitelisted filters, but
            # the pushdown is what we're measuring here.
            event_store.flush()
            sql = (
                "SELECT id, event_type, event_number, occurred_at, received_at, "
                "session_id, run_id, json "
                f"FROM events WHERE session_id = '{sid}' AND outcome = 'failed' "
                "ORDER BY received_at"
            )
            rows = event_store._flight_query(sql)
            from litmus.data.event_store import _parse_event_row

            return [_parse_event_row(r) for r in rows]

        result = benchmark(query_pushdown_failed)
        assert 3_000 <= len(result) <= 3_500

    @pytest.mark.benchmark(
        group="event-payload-filter", warmup=True, min_rounds=30, disable_gc=True
    )
    def test_pushdown_role_dmm_10k(self, event_store: EventStore, benchmark):
        """Pushdown variant of ``test_query_by_role_10k``.

        ``event_store.events(role=...)`` now ORs three typed columns
        (``role``, ``instrument_role``, ``channel_id LIKE 'role.%'``)
        in SQL instead of pulling rows and running
        ``event_matches_role`` per row in Python.
        """
        sid = uuid4()
        from litmus.data.events import InstrumentConnected

        for i in range(10_000):
            if i % 4 == 0:
                event_store.emit(
                    InstrumentConnected(
                        session_id=sid,
                        role="dmm",
                        instrument_id=f"dmm_{i}",
                        resource="GPIB::16",
                    )
                )
            else:
                event_store.emit(_make_measurement(sid, i))

        def query_pushdown_role():
            return event_store.events(session_id=sid, role="dmm")

        result = benchmark(query_pushdown_role)
        assert 2_400 <= len(result) <= 2_600


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


# ===========================================================================
# FileStore — write/read throughput by value shape and size
# ===========================================================================


class TestFileStorePerf:
    """FileStore write / read latency across value shapes and sizes.

    Surfaces the typical T&M shapes: small bytes / Pydantic / ndarray /
    PIL.Image / DataFrame, then sizes (1 KB → 10 MB) so a release-prep
    audit can see where the sustained-write rate breaks.
    """

    @pytest.mark.benchmark(group="filestore-write")
    @pytest.mark.parametrize("size_kb", [1, 100, 1024, 10240])
    def test_write_bytes(self, tmp_path: Path, benchmark, size_kb: int):
        """Write raw bytes blobs. Lower bound on FileStore throughput —
        no serialization cost beyond the disk write + sidecar metadata."""
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        payload = b"x" * (size_kb * 1024)

        def write_one() -> None:
            store.write(f"blob_{uuid4().hex[:8]}", payload, session_id=sid)

        benchmark(write_one)

    @pytest.mark.benchmark(group="filestore-write")
    @pytest.mark.parametrize("size_kb", [1, 100, 1024])
    def test_write_ndarray(self, tmp_path: Path, benchmark, size_kb: int):
        """Write a numpy ndarray (float64). Exercises the .npy serializer
        + sidecar metadata + atomic file write."""
        import numpy as np  # noqa: PLC0415

        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        n_floats = (size_kb * 1024) // 8  # float64 = 8 bytes
        arr = np.random.default_rng(0).normal(size=n_floats)

        def write_one() -> None:
            store.write(f"arr_{uuid4().hex[:8]}", arr, session_id=sid)

        benchmark(write_one)

    @pytest.mark.benchmark(group="filestore-write")
    def test_write_waveform(self, tmp_path: Path, benchmark):
        """Write a Waveform — exercises the .npz serializer with the
        canonical Y / dt / t0 / attributes shape used by example 08."""
        import numpy as np  # noqa: PLC0415

        from litmus.data.files.store import FileStore
        from litmus.data.models import Waveform

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        y = np.random.default_rng(0).normal(size=10_000)
        wf = Waveform(Y=y.tolist(), dt=1e-6)

        def write_one() -> None:
            store.write(f"wf_{uuid4().hex[:8]}", wf, session_id=sid)

        benchmark(write_one)

    @pytest.mark.benchmark(group="filestore-read")
    @pytest.mark.parametrize("size_kb", [1, 100, 1024])
    def test_read_bytes(self, tmp_path: Path, benchmark, size_kb: int):
        """Read a previously-written bytes blob via FileStore.resolve_uri
        + file open. Models the operator UI / RunsQuery hot path."""
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        payload = b"y" * (size_kb * 1024)
        uri = store.write("readme", payload, session_id=sid)

        def read_one() -> None:
            path = store.resolve_uri(uri)
            assert path is not None
            with path.open("rb") as fh:
                _ = fh.read()

        benchmark(read_one)

    @pytest.mark.benchmark(group="filestore-resolve")
    def test_resolve_uri_warm(self, tmp_path: Path, benchmark):
        """Pure ``resolve_uri`` cost. Models the worst-case for retention
        / pruning passes that resolve many URIs in a tight loop."""
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        uri = store.write("zzz", b"k", session_id=sid)

        def resolve_one() -> None:
            _ = store.resolve_uri(uri)

        benchmark(resolve_one)


# ===========================================================================
# Streaming — sustained throughput per format × chunk size
# ===========================================================================


class TestFileStreamPerf:
    """Sustained-write rate of ``files.stream(format=...)`` for each
    built-in format. Measures total time to write ``n_chunks`` of a given
    size; the OPS column reports chunks/s and the per-test reason carries
    the implied bytes/s.

    Tests are scoped to one format × one chunk size so the regression
    surface is clear. Pick the dial that matters per format.
    """

    @pytest.mark.benchmark(group="filestream-raw", warmup=True, min_rounds=10)
    @pytest.mark.parametrize("chunk_kb", [1, 64, 1024])
    def test_stream_raw(self, tmp_path: Path, benchmark, chunk_kb: int):
        """Raw byte stream — lowest-level streaming path. Bound on the
        platform's sustained byte rate before format overhead."""
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        chunk = b"a" * (chunk_kb * 1024)
        n_chunks = 64

        def stream_one() -> None:
            sink = store.open_stream(name=f"raw_{uuid4().hex[:8]}", format="raw", session_id=sid)
            for _ in range(n_chunks):
                sink.write(chunk)
            sink.close()

        benchmark(stream_one)

    @pytest.mark.benchmark(group="filestream-jsonl", warmup=True, min_rounds=10)
    @pytest.mark.parametrize("rows_per_chunk", [10, 100, 1000])
    def test_stream_jsonl(self, tmp_path: Path, benchmark, rows_per_chunk: int):
        """JSONL stream — typical pattern for accumulating typed event
        rows or per-vector measurement dumps."""
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        row = {"t": 1.23, "v": 4.56, "label": "scope_ch1", "ok": True}
        chunk = [row] * rows_per_chunk
        n_chunks = 32

        def stream_one() -> None:
            sink = store.open_stream(
                name=f"jsonl_{uuid4().hex[:8]}", format="jsonl", session_id=sid
            )
            for _ in range(n_chunks):
                sink.write(chunk)
            sink.close()

        benchmark(stream_one)

    @pytest.mark.benchmark(group="filestream-tdms", warmup=True, min_rounds=10)
    def test_stream_tdms(self, tmp_path: Path, benchmark):
        """TDMS stream — NI-shape acquisition. Skipped when nptdms isn't
        installed (optional extra)."""
        nptdms = pytest.importorskip("nptdms")
        import numpy as np  # noqa: PLC0415

        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        data = np.random.default_rng(0).normal(size=10_000)
        n_chunks = 16

        def stream_one() -> None:
            sink = store.open_stream(name=f"tdms_{uuid4().hex[:8]}", format="tdms", session_id=sid)
            for _ in range(n_chunks):
                sink.write(nptdms.ChannelObject("daq", "voltage", data))
            sink.close()

        benchmark(stream_one)

    @pytest.mark.benchmark(group="filestream-h5", warmup=True, min_rounds=10)
    def test_stream_h5(self, tmp_path: Path, benchmark):
        """HDF5 stream — long-term archival shape. Skipped when h5py
        isn't installed (optional extra)."""
        h5py = pytest.importorskip("h5py")
        del h5py
        import numpy as np  # noqa: PLC0415

        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        chunk = {"voltage": np.random.default_rng(0).normal(size=10_000)}
        n_chunks = 16

        def stream_one() -> None:
            sink = store.open_stream(name=f"h5_{uuid4().hex[:8]}", format="h5", session_id=sid)
            for _ in range(n_chunks):
                sink.write(chunk)
            sink.close()

        benchmark(stream_one)


class TestChannelStreamPerf:
    """Sustained-write rate of ``channels.stream`` interactive sample
    push. The single-write call latency is already covered by
    ``test_write_scalars`` above; this measures the sink-context-
    managed shape used by ``examples/09-instrument-streaming``."""

    @pytest.mark.benchmark(group="channelstream", warmup=True, min_rounds=10)
    @pytest.mark.parametrize("n_samples", [100, 1_000, 10_000])
    def test_stream_scalars(self, tmp_path: Path, benchmark, n_samples: int):
        """Same total samples as ``test_write_scalars`` but via the
        ``with channels.stream(name) as sink: sink.write(v)`` shape, so
        the comparison surfaces context-manager overhead per write."""
        import random

        from litmus.data.channels.store import ChannelStore
        from litmus.execution._state import set_channel_store

        store = ChannelStore(tmp_path, uuid4(), flush_threshold=50)
        store.open()
        # Wire the ContextVar so litmus.channels.stream finds the store.
        set_channel_store(store)
        try:
            import litmus.channels as channels_mod  # noqa: PLC0415

            samples = [random.gauss(0, 1) for _ in range(n_samples)]

            def stream_one() -> None:
                with channels_mod.stream("dmm.voltage") as sink:
                    for v in samples:
                        sink.write(v)

            benchmark(stream_one)
        finally:
            set_channel_store(None)
            store.close()
