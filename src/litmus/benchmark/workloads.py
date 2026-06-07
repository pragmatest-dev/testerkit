"""Store workloads — the single definition of what we measure.

Both ``litmus benchmark`` and the perf test suite build their cases from
this module, so user numbers and CI numbers come from identical code.

Each operation is swept across a list of ``scales`` (units of work) and,
for writes, a list of ``writers`` (concurrency) — every combination is a
separate :class:`Workload` case, hence a separate row in the report.

Every workload measures the SHIPPED path: channel queries route through
the channels daemon (not a disk glob); file resolves route through the
catalog daemon (not a date-dir walk). A user's number matches what they
actually experience.
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
    """A finalized TestRun with ``n_steps`` steps x ``n_meas`` measurements."""
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
                            Measurement(name=f"m_{m}", value=3.3 + m * 0.01, outcome=Outcome.PASSED)
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


def _setup_events_emit(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.event_store import EventStore

        es: EventStore = ctx.track(EventStore(_data_dir=ctx.data_dir))  # type: ignore[assignment]

        def emit_all() -> None:
            # Fresh session + flush() per call so the timed work is the
            # DURABLE path (written to disk AND acked by the daemon), not
            # an in-memory batch fill.
            sid = uuid4()
            for i in range(scale):
                es.emit(make_measurement(sid, i))
            es.flush()

        return emit_all

    return setup


def _setup_events_query(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.event_store import EventStore

        es: EventStore = ctx.track(EventStore(_data_dir=ctx.data_dir))  # type: ignore[assignment]
        sid = uuid4()
        for i in range(scale):
            es.emit(make_measurement(sid, i))

        def query_all() -> object:
            return es.events(session_id=sid)

        return query_all

    return setup


# ---------------------------------------------------------------------------
# Runs (the materialized view of events)
# ---------------------------------------------------------------------------


def _setup_runs_save(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.backends.parquet import ParquetBackend
        from litmus.data.run_store import RunStore

        backend = ParquetBackend(data_dir=ctx.data_dir)
        store = ctx.track(RunStore(_data_dir=ctx.data_dir))

        def save_n() -> None:
            for _ in range(scale):
                store.notify_new_run(backend.save_test_run(build_run(uuid4().int % 1_000_000)))  # type: ignore[union-attr]

        return save_n

    return setup


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


def _setup_runs_list(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.analysis.runs_query import RunsQuery

        _populate_runs(ctx, scale)

        def list_page() -> object:
            with RunsQuery(_data_dir=ctx.data_dir) as q:
                return q.list_recent(limit=50)

        return list_page

    return setup


def _setup_runs_steps(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.analysis.runs_query import RunsQuery
        from litmus.analysis.steps_query import StepsQuery

        _populate_runs(ctx, scale)
        with RunsQuery(_data_dir=ctx.data_dir) as q:
            rows = q.list_recent(limit=1)
        run_id = str(rows[0].run_id) if rows else ""

        def steps() -> object:
            with StepsQuery(_data_dir=ctx.data_dir) as q:
                return q.list_for_run(run_id)

        return steps

    return setup


# ---------------------------------------------------------------------------
# Channels — write through a serve producer, query the daemon
# ---------------------------------------------------------------------------


def _setup_channels_write(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.channels.store import ChannelStore

        store = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=100, serve=True)
        store.open()
        ctx.track(store)

        def write_all() -> None:
            for i in range(scale):
                store.write("sensor.temp", 25.0 + i * 0.01, units="C")

        return write_all

    return setup


def _setup_channels_query(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.channels import flight_manager
        from litmus.data.channels.client import ChannelClient
        from litmus.data.channels.store import ChannelStore

        # Unique channel per case — all channel cases share the temp dir's
        # one daemon/index, so a fixed name would let this query count
        # samples written by other cases (polluting the row count).
        channel_id = f"bench.{uuid4().hex[:8]}"
        producer = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=100, serve=True)
        producer.open()
        ctx.track(producer)
        for i in range(scale):
            producer.write(channel_id, 25.0 + i * 0.01, units="C")

        channels_dir = ctx.data_dir / "channels"
        location = flight_manager.acquire(channels_dir)
        client = ChannelClient(location)
        ctx.track(client)
        ctx.track(_Releaser(lambda: flight_manager.release(channels_dir)))

        def query() -> object:
            return client.query(channel_id)

        return query

    return setup


def _setup_channels_stream(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        import litmus.channels as channels_mod
        from litmus.data.channels.store import ChannelStore
        from litmus.execution._state import set_channel_store

        store = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=50, serve=True)
        store.open()
        ctx.track(store)
        set_channel_store(store)
        ctx.track(_Releaser(lambda: set_channel_store(None)))
        values = [25.0 + (i % 100) * 0.01 for i in range(scale)]

        def stream_one() -> None:
            with channels_mod.stream("dmm.voltage") as sink:
                for v in values:
                    sink.write(v)

        return stream_one

    return setup


# ---------------------------------------------------------------------------
# Files — object store + catalog daemon
# ---------------------------------------------------------------------------


def _acquire_catalog(ctx: BenchContext):
    from litmus.data.files.catalog_manager import acquire, release

    files_dir = ctx.data_dir / "files"
    acquire(files_dir)
    ctx.track(_Releaser(lambda: release(files_dir)))
    return files_dir


def _setup_files_write(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.store import FileStore

        _acquire_catalog(ctx)
        store = FileStore(data_dir=ctx.data_dir)
        sid = uuid4().hex
        payload = b"x" * (100 * 1024)  # 100 KB blobs

        def write_n() -> None:
            for _ in range(scale):
                store.write(f"blob_{uuid4().hex[:8]}", payload, session_id=sid)

        return write_n

    return setup


def _setup_files_resolve(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.catalog_manager import resolve_uri
        from litmus.data.files.store import FileStore

        files_dir = _acquire_catalog(ctx)
        store = FileStore(data_dir=ctx.data_dir)
        uri = store.write("resolveme", b"x" * 256, session_id=uuid4().hex)

        def resolve() -> object:
            return resolve_uri(files_dir, uri)

        return resolve

    return setup


def _setup_files_list(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.catalog_manager import list_recent
        from litmus.data.files.store import FileStore

        files_dir = _acquire_catalog(ctx)
        store = FileStore(data_dir=ctx.data_dir)
        sid = uuid4().hex
        for i in range(scale):
            store.write(f"list_{i}_{uuid4().hex[:6]}", b"y" * 128, session_id=sid)

        def listing() -> object:
            return list_recent(files_dir, 50)

        return listing

    return setup


def _setup_files_stream_raw(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.files.store import FileStore

        store = FileStore(data_dir=ctx.data_dir)
        sid = uuid4().hex
        chunk = b"a" * (64 * 1024)  # 64 KB chunks

        def stream_one() -> None:
            sink = store.open_stream(name=f"raw_{uuid4().hex[:8]}", format="raw", session_id=sid)
            for _ in range(scale):
                sink.write(chunk)
            sink.close()

        return stream_one

    return setup


# ---------------------------------------------------------------------------
# Case registry — sweep scales (units) and writers (concurrency)
# ---------------------------------------------------------------------------

# Per-operation: (store, unit, setup-factory, fast-scales, full-scales).
# A row is emitted for every scale at 1 writer.
_OPS: dict[str, tuple] = {
    "events.emit": ("events", "events", _setup_events_emit, [100, 1000], [100, 1000, 10000]),
    "events.query": ("events", "events", _setup_events_query, [100, 1000], [100, 1000, 10000]),
    "channels.write": (
        "channels",
        "samples",
        _setup_channels_write,
        [100, 1000],
        [100, 1000, 10000],
    ),
    "channels.query": (
        "channels",
        "samples",
        _setup_channels_query,
        [100, 1000],
        [100, 1000, 10000],
    ),
    "channels.stream": (
        "channels",
        "samples",
        _setup_channels_stream,
        [100, 1000],
        [100, 1000, 10000],
    ),
    "runs.save": ("runs", "runs", _setup_runs_save, [1, 10], [1, 10, 100]),
    "runs.list": ("runs", "runs", _setup_runs_list, [10, 100], [10, 100, 1000]),
    "runs.steps": ("runs", "runs", _setup_runs_steps, [10, 100], [10, 100, 1000]),
    "files.write": ("files", "artifacts", _setup_files_write, [1, 10], [1, 10, 100]),
    "files.resolve": ("files", "files", _setup_files_resolve, [1], [1]),
    "files.list": ("files", "files", _setup_files_list, [10, 100], [10, 100, 1000]),
    "files.stream_raw": ("files", "chunks", _setup_files_stream_raw, [16, 64], [16, 64, 256]),
}

# Write ops get a concurrency sweep at one representative scale (which is
# also present in the 1-writer scale sweep, so speedup has a baseline).
_CONCURRENCY: dict[str, int] = {
    "events.emit": 1000,
    "channels.write": 1000,
    "files.write": 10,
    "runs.save": 10,
}


def build_cases(tier: str = "fast") -> list[Workload]:
    """All cases for ``tier``: scale sweep (1 writer) + concurrency sweep."""
    full = tier == "full"
    writer_counts = [2, 4] if full else [2]
    cases: list[Workload] = []
    for op, (store, unit, factory, fast_scales, full_scales) in _OPS.items():
        scales = full_scales if full else fast_scales
        for scale in scales:
            cases.append(
                Workload(op, store, unit, scale, writers=1, setup=factory(scale), tier=tier)
            )
    for op, rep_scale in _CONCURRENCY.items():
        store = _OPS[op][0]
        unit = _OPS[op][1]
        for w in writer_counts:
            cases.append(Workload(op, store, unit, rep_scale, writers=w, setup=None, tier=tier))
    return cases
