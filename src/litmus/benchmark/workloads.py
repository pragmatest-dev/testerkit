"""Store workloads — the single definition of what we measure.

Both ``litmus benchmark`` (via :mod:`litmus.benchmark.runner`) and the
perf test suite (``tests/test_data/test_perf.py``) build their work from
this module, so the numbers a user reports and the numbers CI gates on
come from identical code paths.

Every workload here measures the SHIPPED path — the one a real install
uses. Channel queries route through the channels daemon (not a disk
glob); file resolves route through the catalog daemon (not a date-dir
walk). A user's number therefore matches what they actually experience.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from litmus.benchmark.core import BenchContext, Workload


class _Releaser:
    """Tiny closeable that runs ``fn`` on teardown (for daemon releases)."""

    def __init__(self, fn: Callable[[], object]) -> None:
        self._fn = fn

    def close(self) -> None:
        self._fn()


# ---------------------------------------------------------------------------
# Sample data builders (shared with the perf tests)
# ---------------------------------------------------------------------------


def make_measurement(session_id: UUID, i: int):
    """A representative MeasurementRecorded event."""
    from litmus.data.events import MeasurementRecorded

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


def build_run(seed: int, *, n_steps: int = 10, n_meas: int = 5):
    """A finalized TestRun with ``n_steps`` steps × ``n_meas`` measurements."""
    from litmus.data.models import (
        DUT,
        Measurement,
        Outcome,
        TestRun,
        TestStep,
        TestVector,
    )

    return TestRun(
        id=uuid4(),
        started_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 6, 1, 12, 1, 0, tzinfo=UTC),
        dut=DUT(serial=f"SN-{seed:06d}"),
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name=f"step_{s}",
                outcome=Outcome.PASSED,
                started_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
                ended_at=datetime(2026, 6, 1, 12, 0, 30, tzinfo=UTC),
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        measurements=[
                            Measurement(
                                name=f"m_{m}",
                                value=3.3 + m * 0.01,
                                outcome=Outcome.PASSED,
                            )
                            for m in range(n_meas)
                        ],
                    )
                ],
            )
            for s in range(n_steps)
        ],
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _setup_events_emit(n: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.event_store import EventStore

        es: EventStore = ctx.track(EventStore(_data_dir=ctx.data_dir))  # type: ignore[assignment]

        def emit_all() -> None:
            # Fresh session per call + flush() so the timed work is the
            # DURABLE path (events written to disk AND acked by the
            # daemon), not just an in-memory batch fill. This is the
            # number that compares apples-to-apples with the concurrency
            # probe and reflects what a station actually sustains.
            sid = uuid4()
            for i in range(n):
                es.emit(make_measurement(sid, i))
            es.flush()

        return emit_all

    return setup


def _setup_events_query(n: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.event_store import EventStore

        es: EventStore = ctx.track(EventStore(_data_dir=ctx.data_dir))  # type: ignore[assignment]
        sid = uuid4()
        for i in range(n):
            es.emit(make_measurement(sid, i))

        def query_all() -> object:
            return es.events(session_id=sid)

        return query_all

    return setup


# ---------------------------------------------------------------------------
# Runs (the materialized view of events)
# ---------------------------------------------------------------------------


def _setup_runs_save(ctx: BenchContext) -> Callable[[], object]:
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.run_store import RunStore

    backend = ParquetBackend(data_dir=ctx.data_dir)
    store = ctx.track(RunStore(_data_dir=ctx.data_dir))

    def save_one() -> None:
        store.notify_new_run(backend.save_test_run(build_run(uuid4().int % 1_000_000)))  # type: ignore[union-attr]

    return save_one


def _populate_runs(ctx: BenchContext, n: int) -> None:
    from litmus.data.backends.parquet import ParquetBackend
    from litmus.data.run_store import RunStore

    backend = ParquetBackend(data_dir=ctx.data_dir)
    store = RunStore(_data_dir=ctx.data_dir)
    try:
        for i in range(n):
            store.notify_new_run(backend.save_test_run(build_run(i)))
    finally:
        store.close()


def _setup_runs_list(populate: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.analysis.runs_query import RunsQuery

        _populate_runs(ctx, populate)

        def list_page() -> object:
            with RunsQuery(_data_dir=ctx.data_dir) as q:
                return q.list_recent(limit=50)

        return list_page

    return setup


def _setup_runs_steps(populate: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.analysis.runs_query import RunsQuery
        from litmus.analysis.steps_query import StepsQuery

        _populate_runs(ctx, populate)
        with RunsQuery(_data_dir=ctx.data_dir) as q:
            rows = q.list_recent(limit=1)
        run_id = str(rows[0].run_id) if rows else ""

        def steps() -> object:
            with StepsQuery(_data_dir=ctx.data_dir) as q:
                return q.list_for_run(run_id)

        return steps

    return setup


# ---------------------------------------------------------------------------
# Channels (time-series) — write through a serve producer, query the daemon
# ---------------------------------------------------------------------------


def _setup_channels_write(n: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.channels.store import ChannelStore

        store = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=100, serve=True)
        store.open()
        ctx.track(store)

        def write_all() -> None:
            for i in range(n):
                store.write("sensor.temp", 25.0 + i * 0.01, units="°C")

        return write_all

    return setup


def _setup_channels_query(populate: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.channels import flight_manager
        from litmus.data.channels.client import ChannelClient
        from litmus.data.channels.store import ChannelStore

        producer = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=100, serve=True)
        producer.open()
        ctx.track(producer)
        for i in range(populate):
            producer.write("sensor.temp", 25.0 + i * 0.01, units="°C")

        channels_dir = ctx.data_dir / "channels"
        location = flight_manager.acquire(channels_dir)
        client = ChannelClient(location)
        ctx.track(client)
        ctx.track(_Releaser(lambda: flight_manager.release(channels_dir)))

        def query() -> object:
            return client.query("sensor.temp")

        return query

    return setup


def _setup_channels_stream(n: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        import litmus.channels as channels_mod
        from litmus.data.channels.store import ChannelStore
        from litmus.execution._state import set_channel_store

        store = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=50, serve=True)
        store.open()
        ctx.track(store)
        set_channel_store(store)
        ctx.track(_Releaser(lambda: set_channel_store(None)))
        values = [25.0 + (i % 100) * 0.01 for i in range(n)]

        def stream_one() -> None:
            with channels_mod.stream("dmm.voltage") as sink:
                for v in values:
                    sink.write(v)

        return stream_one

    return setup


# ---------------------------------------------------------------------------
# Files (object store + catalog daemon)
# ---------------------------------------------------------------------------


def _files_dir(ctx: BenchContext):
    return ctx.data_dir / "files"


def _acquire_catalog(ctx: BenchContext):
    from litmus.data.files.catalog_manager import acquire, release

    files_dir = _files_dir(ctx)
    acquire(files_dir)
    ctx.track(_Releaser(lambda: release(files_dir)))
    return files_dir


def _setup_files_write(size_kb: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.store import FileStore

        _acquire_catalog(ctx)
        store = FileStore(data_dir=ctx.data_dir)
        sid = uuid4().hex
        payload = b"x" * (size_kb * 1024)

        def write_one() -> None:
            store.write(f"blob_{uuid4().hex[:8]}", payload, session_id=sid)

        return write_one

    return setup


def _setup_files_resolve(ctx: BenchContext) -> Callable[[], object]:
    from litmus.data.files.catalog_manager import resolve_uri
    from litmus.data.files.store import FileStore

    files_dir = _acquire_catalog(ctx)
    store = FileStore(data_dir=ctx.data_dir)
    uri = store.write("resolveme", b"x" * 256, session_id=uuid4().hex)

    def resolve() -> object:
        return resolve_uri(files_dir, uri)

    return resolve


def _setup_files_list(populate: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.catalog_manager import list_recent
        from litmus.data.files.store import FileStore

        files_dir = _acquire_catalog(ctx)
        store = FileStore(data_dir=ctx.data_dir)
        sid = uuid4().hex
        for i in range(populate):
            store.write(f"list_{i}_{uuid4().hex[:6]}", b"y" * 128, session_id=sid)

        def listing() -> object:
            return list_recent(files_dir, 50)

        return listing

    return setup


def _setup_files_stream_raw(
    chunk_kb: int, n_chunks: int
) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=ctx.data_dir)
        sid = uuid4().hex
        chunk = b"a" * (chunk_kb * 1024)

        def stream_one() -> None:
            sink = store.open_stream(name=f"raw_{uuid4().hex[:8]}", format="raw", session_id=sid)
            for _ in range(n_chunks):
                sink.write(chunk)
            sink.close()

        return stream_one

    return setup


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def all_workloads() -> list[Workload]:
    """Every workload, fast and full tier."""
    return [
        # Events
        Workload(
            "events.emit",
            "events",
            "Emit + flush measurement events",
            "events",
            300,
            _setup_events_emit(300),
        ),
        Workload(
            "events.query",
            "events",
            "Query 1k events by session",
            "queries",
            1,
            _setup_events_query(1_000),
        ),
        # Runs
        Workload(
            "runs.save",
            "runs",
            "Save + index a run (10 steps × 5 meas)",
            "runs",
            1,
            _setup_runs_save,
        ),
        Workload("runs.list", "runs", "List 50 recent runs", "queries", 1, _setup_runs_list(25)),
        Workload(
            "runs.steps", "runs", "Steps tree for one run", "queries", 1, _setup_runs_steps(25)
        ),
        # Channels
        Workload(
            "channels.write",
            "channels",
            "Write scalar samples (producer)",
            "samples",
            500,
            _setup_channels_write(500),
        ),
        Workload(
            "channels.query",
            "channels",
            "Query a channel (daemon index)",
            "queries",
            1,
            _setup_channels_query(1000),
        ),
        Workload(
            "channels.stream",
            "channels",
            "Stream samples via sink",
            "samples",
            500,
            _setup_channels_stream(500),
        ),
        # Files
        Workload(
            "files.write", "files", "Write a 100 KB blob", "artifacts", 1, _setup_files_write(100)
        ),
        Workload(
            "files.resolve",
            "files",
            "Resolve a URI (daemon catalog)",
            "queries",
            1,
            _setup_files_resolve,
        ),
        Workload(
            "files.list",
            "files",
            "List recent files (daemon catalog)",
            "queries",
            1,
            _setup_files_list(20),
        ),
        Workload(
            "files.stream_raw",
            "files",
            "Stream raw bytes (64 × 64 KB)",
            "chunks",
            64,
            _setup_files_stream_raw(64, 64),
        ),
    ]


def fast_workloads() -> list[Workload]:
    return [w for w in all_workloads() if w.tier == "fast"]
