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

from litmus.benchmark.core import BenchContext
from litmus.benchmark.workloads import build_cases
from litmus.benchmark.workloads import build_run as _build_run
from litmus.benchmark.workloads import make_measurement as _make_measurement
from litmus.data.channels.store import ChannelStore
from litmus.data.event_store import EventStore
from litmus.data.events import SessionStarted

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


# ``_make_measurement`` and ``_build_run`` are imported from
# ``litmus.benchmark.workloads`` (above) — the SAME builders the
# ``litmus benchmark`` CLI uses, so CI numbers and user-reported numbers
# can never drift. The registry-driven ``TestSharedWorkloads`` below
# exercises every shipped workload through that single definition.


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
            assert store.read(uri) is not None

        benchmark(read_one)

    @pytest.mark.benchmark(group="filestore-resolve")
    def test_locate_uri_warm(self, tmp_path: Path, benchmark):
        """Pure locate cost (resolve key + size, no byte read). Models the
        worst-case for retention / pruning passes that locate many URIs in a
        tight loop."""
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        uri = store.write("zzz", b"k", session_id=sid)

        def locate_one() -> None:
            _ = store.size(uri)

        benchmark(locate_one)


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

    def test_stream_raw_near_io_ceiling(self, tmp_path: Path) -> None:
        """Raw streaming stays close to the raw ``open(...,'wb').write`` ceiling
        on the same storage. Regression guard for the publish_frame-on-the-
        hot-path disease — a Flight ``do_put`` per chunk dropped streaming to
        ~16% of the ceiling; the non-blocking ``_FrameRelay`` (resolve-once +
        background drain) restored it to ~90%+. Sampling is interleaved
        warm-vs-warm with the GC paused and min-of-N, so the ratio reflects the
        real per-chunk code overhead, not disk write-back noise.
        """
        import gc  # noqa: PLC0415
        import time  # noqa: PLC0415

        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=tmp_path)
        sid = str(uuid4())
        n_chunks = 200
        chunk = b"a" * (64 * 1024)

        def raw() -> None:
            with open(tmp_path / f"r_{uuid4().hex}.bin", "wb") as f:
                for _ in range(n_chunks):
                    f.write(chunk)

        def streaming() -> None:
            sink = store.open_stream(name=f"s_{uuid4().hex[:8]}", format="raw", session_id=sid)
            for _ in range(n_chunks):
                sink.write(chunk)
            sink.close()

        for _ in range(2):  # warm caches for both paths
            raw()
            streaming()
        raw_t: list[float] = []
        stream_t: list[float] = []
        gc.disable()
        try:
            for _ in range(6):  # interleave so both see the same cache state
                t = time.perf_counter()
                raw()
                raw_t.append(time.perf_counter() - t)
                t = time.perf_counter()
                streaming()
                stream_t.append(time.perf_counter() - t)
        finally:
            gc.enable()

        ratio = min(raw_t) / min(stream_t)  # ≈ streaming throughput / raw throughput
        assert ratio >= 0.70, (
            f"raw streaming is only {ratio:.0%} of the file-I/O ceiling "
            "— is publish_frame back on the writer's hot path?"
        )

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


# ===========================================================================
# Concurrency — does the singleton daemon path scale with N writers?
# ===========================================================================


def _writer_event_worker(n_events: int, seed: int) -> tuple[float, int]:
    """One process's worth of event writes against the canonical EventStore.

    Returns (wall_seconds, ok_count). Spawned via multiprocessing.Process
    so the worker hits the real daemon RPC path the way pytest workers
    in multi-slot mode would.
    """
    import time
    from uuid import uuid4

    from litmus.data.event_store import EventStore
    from litmus.data.events import MeasurementRecorded

    store = EventStore()
    sid = uuid4()
    ok = 0
    t0 = time.monotonic()
    try:
        for i in range(n_events):
            store.emit(
                MeasurementRecorded(
                    session_id=sid,
                    step_name=f"step_{i % 10}",
                    step_index=i % 10,
                    measurement_name=f"voltage_{seed}_{i}",
                    value=3.3 + ((seed * 1000 + i) % 100) * 0.01,
                    units="V",
                    outcome="passed",
                    limit_low=3.0,
                    limit_high=3.6,
                )
            )
            ok += 1
    finally:
        store.close()
    return time.monotonic() - t0, ok


def _writer_channel_worker(n_samples: int, seed: int) -> tuple[float, int]:
    """One process writing ``n_samples`` scalars to a per-worker channel.

    Channel name carries ``seed`` so writers don't collide on the same
    channel descriptor — measures pure RPC + per-channel-buffer
    contention, not first-write-registry-pinning contention.
    """
    import tempfile
    import time
    from pathlib import Path
    from uuid import uuid4

    from litmus.data.channels.store import ChannelStore

    store_dir = Path(tempfile.mkdtemp(prefix="litmus_perf_ch_"))
    store = ChannelStore(store_dir, uuid4(), flush_threshold=50)
    store.open()
    ok = 0
    t0 = time.monotonic()
    try:
        for i in range(n_samples):
            store.write(f"dmm.voltage_w{seed}", 3.3 + (i % 100) * 0.01)
            ok += 1
    finally:
        store.close()
    return time.monotonic() - t0, ok


def _writer_filestore_worker(n_artifacts: int, payload_kb: int, seed: int) -> tuple[float, int]:
    """One process writing ``n_artifacts`` blobs through the per-process FileStore."""
    import time
    from uuid import uuid4

    from litmus.data.files.store import FileStore

    store = FileStore()
    sid = str(uuid4())
    payload = (f"w{seed}-".encode() * (payload_kb * 1024 // 8))[: payload_kb * 1024]
    ok = 0
    t0 = time.monotonic()
    for i in range(n_artifacts):
        store.write(f"art_{seed}_{i:04d}", payload, session_id=sid)
        ok += 1
    return time.monotonic() - t0, ok


class TestConcurrencyPerf:
    """Multi-process scaling tests for each store.

    Each test spawns ``n_writers`` subprocesses and measures the total
    wall-clock time. The OPS column shows total writes / total wall —
    so linear scaling shows up as a flat OPS vs n_writers curve, and
    mutex contention shows up as OPS dropping with n_writers.

    Numbers feed the v0.2.0 performance-limits doc's "where does the
    singleton daemon start to bite?" section.
    """

    @pytest.mark.benchmark(group="concurrent-event-emit", warmup=False, min_rounds=3)
    @pytest.mark.parametrize("n_writers", [1, 2, 4])
    def test_n_writers_event_emit(self, benchmark, n_writers: int) -> None:
        """N processes each emit 500 MeasurementRecorded events.

        Total = n_writers × 500. Singleton EventStore daemon serves all
        of them; the Flight server is the contention point. Linear OPS
        across n_writers means the daemon is RPC-bound, not lock-bound.
        """
        from multiprocessing import get_context

        n_per = 500

        def run_concurrent() -> int:
            with get_context("spawn").Pool(n_writers) as pool:
                results = pool.starmap(_writer_event_worker, [(n_per, w) for w in range(n_writers)])
            ok_total = sum(ok for _, ok in results)
            assert ok_total == n_writers * n_per
            return ok_total

        benchmark(run_concurrent)

    @pytest.mark.benchmark(group="concurrent-event-emit")
    def test_event_writes_scale_sublinearly(self) -> None:
        """GATE (design goal #4): events writes PARALLELIZE, not serialize.

        Lock-free cursor-per-thread writes let 4 writers beat 1 on
        aggregate emit throughput (measured ~2.4x). If the daemon's write
        path ever regresses to serialized (e.g. a reintroduced global
        write lock — exactly the 2a regression that shipped uncaught),
        4-writer throughput collapses to ~1-writer and this FAILS. The
        1.5x bar sits well below the ~2.4x measurement, so it gates the
        regression without flaking on noise. Best-of-3 sheds cold-spawn /
        scheduler outliers. This is the guard whose absence let goal #4
        silently regress before.
        """
        from multiprocessing import get_context

        n_per = 500

        def best_throughput(n_writers: int) -> float:
            best = 0.0
            for _ in range(3):
                with get_context("spawn").Pool(n_writers) as pool:
                    results = pool.starmap(
                        _writer_event_worker, [(n_per, w) for w in range(n_writers)]
                    )
                assert sum(ok for _, ok in results) == n_writers * n_per
                wall = max(w for w, _ in results)  # concurrent run → slowest worker = wall
                best = max(best, (n_writers * n_per) / wall)
            return best

        t1 = best_throughput(1)
        t4 = best_throughput(4)
        ratio = (t4 / t1) if t1 else 0.0
        assert ratio >= 1.5, (
            f"events writes not parallel: 1w={t1:.0f} ev/s, 4w={t4:.0f} ev/s "
            f"(ratio {ratio:.2f}x, need >=1.5x — did a global write lock regress in?)"
        )

    @pytest.mark.benchmark(group="concurrent-channel-write", warmup=False, min_rounds=3)
    @pytest.mark.parametrize("n_writers", [1, 2, 4])
    def test_n_writers_channel_scalars(self, benchmark, n_writers: int) -> None:
        """N processes each write 500 scalar samples to their own channel.

        Per-channel buffers; the ChannelStore Flight server fan-in is the
        contention point. Per-worker channel names so no first-write
        descriptor-pinning race.
        """
        from multiprocessing import get_context

        n_per = 500

        def run_concurrent() -> int:
            with get_context("spawn").Pool(n_writers) as pool:
                results = pool.starmap(
                    _writer_channel_worker, [(n_per, w) for w in range(n_writers)]
                )
            ok_total = sum(ok for _, ok in results)
            assert ok_total == n_writers * n_per
            return ok_total

        benchmark(run_concurrent)

    @pytest.mark.benchmark(group="concurrent-file-write", warmup=False, min_rounds=3)
    @pytest.mark.parametrize("n_writers", [1, 2, 4])
    def test_n_writers_filestore(self, benchmark, n_writers: int) -> None:
        """N processes each write 100 × 10 KB blobs.

        FileStore has no daemon — pure OS file-system contention on the
        sidecar atomic rename + ext4 dirent updates. OPS scaling here
        measures the FS path, not RPC.
        """
        from multiprocessing import get_context

        n_per = 100
        payload_kb = 10

        def run_concurrent() -> int:
            with get_context("spawn").Pool(n_writers) as pool:
                results = pool.starmap(
                    _writer_filestore_worker,
                    [(n_per, payload_kb, w) for w in range(n_writers)],
                )
            ok_total = sum(ok for _, ok in results)
            assert ok_total == n_writers * n_per
            return ok_total

        benchmark(run_concurrent)


# ---------------------------------------------------------------------------
# RunStore / Query benchmarks — the materialized view of events
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def populated_runs() -> list[str]:
    """Save + index 100 runs on the canonical store once, for query latency."""
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.data_dir import resolve_data_dir
    from litmus.data.run_store import RunStore

    backend = ParquetBackend(data_dir=resolve_data_dir())
    store = RunStore()
    ids: list[str] = []
    try:
        for i in range(100):
            run = _build_run(i)
            store.notify_new_run(backend.save_test_run(run))
            ids.append(str(run.id))
    finally:
        store.close()
    return ids


class TestRunsPerf:
    """Runs = the materialized view of events. Throughput = save+index
    rate; latency = warm-index reads (list page, steps tree, yield rollup).
    Runs write PARALLELISM rides the events write path — gated separately
    by ``test_event_writes_scale_sublinearly`` — plus the concurrent
    materialize path in :class:`TestRunsConcurrencyPerf`.
    """

    @pytest.mark.benchmark(group="runs-write")
    def test_save_and_index_throughput(self, benchmark) -> None:
        from litmus.data.backends.parquet import ParquetBackend
        from litmus.data.data_dir import resolve_data_dir
        from litmus.data.run_store import RunStore

        backend = ParquetBackend(data_dir=resolve_data_dir())
        store = RunStore()

        def save_one() -> None:
            store.notify_new_run(backend.save_test_run(_build_run(uuid4().int % 1_000_000)))

        try:
            benchmark(save_one)
        finally:
            store.close()

    @pytest.mark.benchmark(group="runs-query", warmup=True, min_rounds=30, disable_gc=True)
    def test_list_recent_latency(self, populated_runs: list[str], benchmark) -> None:
        from litmus.analysis.runs_query import RunsQuery

        def list_page():
            with RunsQuery() as q:
                return q.list_recent(limit=50)

        rows = benchmark(list_page)
        assert len(rows) >= 1

    @pytest.mark.benchmark(group="runs-query", warmup=True, min_rounds=30, disable_gc=True)
    def test_steps_for_run_latency(self, populated_runs: list[str], benchmark) -> None:
        from litmus.analysis.steps_query import StepsQuery

        run_id = populated_runs[0]

        def steps():
            with StepsQuery() as q:
                return q.list_for_run(run_id)

        rows = benchmark(steps)
        assert len(rows) >= 1

    @pytest.mark.benchmark(group="runs-query", warmup=True, min_rounds=30, disable_gc=True)
    def test_yield_summary_latency(self, populated_runs: list[str], benchmark) -> None:
        from litmus.analysis.measurements_query import MeasurementsQuery

        def rollup():
            with MeasurementsQuery() as q:
                return q.yield_summary()

        benchmark(rollup)


def _writer_runs_worker(n_runs: int, seed: int) -> tuple[float, int]:
    """Save + index ``n_runs`` runs; return (wall_seconds, ok_count)."""
    import time

    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.data_dir import resolve_data_dir
    from litmus.data.run_store import RunStore

    backend = ParquetBackend(data_dir=resolve_data_dir())
    store = RunStore()
    ok = 0
    t0 = time.perf_counter()
    try:
        for i in range(n_runs):
            store.notify_new_run(backend.save_test_run(_build_run(seed * 100_000 + i)))
            ok += 1
    finally:
        store.close()
    return time.perf_counter() - t0, ok


class TestRunsConcurrencyPerf:
    """Concurrent run materialization — N processes each save+index runs
    through the singleton runs daemon. Flat OPS vs n_writers = the daemon
    indexes in parallel; dropping OPS = a serialization point.
    """

    @pytest.mark.benchmark(group="concurrent-run-write", warmup=False, min_rounds=3)
    @pytest.mark.parametrize("n_writers", [1, 2, 4])
    def test_n_writers_runs(self, benchmark, n_writers: int) -> None:
        from multiprocessing import get_context

        n_per = 25

        def run_concurrent() -> int:
            with get_context("spawn").Pool(n_writers) as pool:
                results = pool.starmap(_writer_runs_worker, [(n_per, w) for w in range(n_writers)])
            ok_total = sum(ok for _, ok in results)
            assert ok_total == n_writers * n_per
            return ok_total

        benchmark(run_concurrent)


# ---------------------------------------------------------------------------
# Warm-daemon query latency — the Phase C/D additions
# ---------------------------------------------------------------------------


class TestFilesCatalogQueryPerf:
    """Files catalog daemon (Phase D): resolve/list served from the warm
    DuckDB catalog instead of a date-dir / whole-tree walk.
    """

    @pytest.mark.benchmark(group="files-catalog-query", warmup=True, min_rounds=30, disable_gc=True)
    def test_catalog_resolve_latency(self, benchmark) -> None:
        from litmus.data.data_dir import resolve_data_dir
        from litmus.data.files.catalog_manager import acquire, release, resolve_uri
        from litmus.data.files.store import FileStore

        files_dir = resolve_data_dir() / "files"
        acquire(files_dir)
        try:
            store = FileStore()
            sid = uuid4().hex
            uri = store.write(f"perf.cat.{uuid4().hex[:8]}", b"x" * 256, session_id=sid)

            def resolve():
                return resolve_uri(files_dir, uri)

            assert benchmark(resolve) is not None
        finally:
            release(files_dir)

    @pytest.mark.benchmark(group="files-catalog-query", warmup=True, min_rounds=30, disable_gc=True)
    def test_catalog_list_recent_latency(self, benchmark) -> None:
        from litmus.data.data_dir import resolve_data_dir
        from litmus.data.files.catalog_manager import acquire, list_recent, release
        from litmus.data.files.store import FileStore

        files_dir = resolve_data_dir() / "files"
        acquire(files_dir)
        try:
            store = FileStore()
            sid = uuid4().hex
            for i in range(20):
                store.write(f"perf.list.{i}.{uuid4().hex[:6]}", b"y" * 128, session_id=sid)

            def listing():
                return list_recent(files_dir, 50)

            assert len(benchmark(listing)) >= 1
        finally:
            release(files_dir)


# ---------------------------------------------------------------------------
# Shared workloads — the SAME definitions ``litmus benchmark`` runs
#
# These prove the shipped CLI workloads stay green and that the test
# suite and the CLI share one definition (no drift). Each runs against
# the canonical store (the daemon paths the CLI exercises in its temp
# dir), driven through ``litmus.benchmark.workloads``.
# ---------------------------------------------------------------------------


_SHARED_CASES = [c for c in build_cases("fast") if c.writers == 1 and c.setup is not None]


class TestSharedWorkloads:
    """Run every shipped (1-writer) benchmark case through its single definition."""

    @pytest.mark.benchmark(group="shared-workloads", warmup=False, min_rounds=3)
    @pytest.mark.parametrize("case", _SHARED_CASES, ids=lambda c: c.key)
    def test_case(self, benchmark, case) -> None:
        """Every shipped case runs green via the single definition the CLI uses.

        Write cases return ``None``; query cases return a result. The test
        asserts the case executes without error — that the shared workload
        definition still works (no drift between CLI and CI).
        """
        from litmus.data.data_dir import resolve_data_dir

        ctx = BenchContext(resolve_data_dir())
        try:
            fn = case.setup(ctx)
            benchmark(fn)
        finally:
            ctx.close()
