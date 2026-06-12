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
from typing import NamedTuple
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
        UUT,
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
        uut=UUT(serial=f"SN-{seed:06d}"),
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
        # Mirror the real finalize path (``save_test_run`` = atomic file
        # write, no synchronous daemon notify).
        from litmus.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(data_dir=ctx.data_dir)

        def save_n() -> None:
            for _ in range(scale):
                backend.save_test_run(build_run(uuid4().int % 1_000_000))

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


def _setup_channels_block(scale: int) -> Callable[[BenchContext], Callable[[], object]]:
    """The high-rate channel path: one array (waveform block) per write —
    one RPC carries ``scale`` points, vs one RPC per scalar in ``channels.write``."""

    def setup(ctx: BenchContext) -> Callable[[], object]:
        from litmus.data.channels.store import ChannelStore

        store = ChannelStore(ctx.data_dir, uuid4(), flush_threshold=100, serve=True)
        store.open()
        ctx.track(store)
        block = [25.0 + (i % 100) * 0.01 for i in range(scale)]

        def write_block() -> None:
            store.write("scope.ch1", block, sample_interval=1e-6)

        return write_block

    return setup


# ---------------------------------------------------------------------------
# Case registry — sweep scales (units) and writers (concurrency)
# ---------------------------------------------------------------------------


class _Op(NamedTuple):
    store: str
    unit: str
    factory: Callable[[int], Callable[[BenchContext], Callable[[], object]]]
    fast_scales: list[int]
    full_scales: list[int]
    bytes_per_unit: int | None  # set for byte-heavy ops → enables bytes/s


# Write ops include a ``scale=1`` floor anchor so the per-call overhead is
# measured directly (not extrapolated). bytes_per_unit is set only where a
# record has a fixed byte size (channels samples, file blobs/chunks) — for
# those, the report shows bytes/s, which is the real throughput axis.
# Bytes per record, so every op reports a real byte rate. Events/runs are
# measured from a serialized sample; the rest are known fixed sizes.
_EVENT_BYTES = len(make_measurement(uuid4(), 0).model_dump_json().encode())
_RUN_BYTES = len(build_run(0).model_dump_json().encode())
_SCALAR_BYTES = 8  # one float sample / point
_ROW_BYTES = 256  # one summary/metadata row from a query

# Each scale is its own row; the smallest (often 1) is the single-call case
# — its rate is overhead-bound but a valid data point. Call overhead is the
# line's intercept; per-record is the slope.
_OPS: dict[str, _Op] = {
    "events.emit": _Op(
        "events", "events", _setup_events_emit, [1, 1000], [1, 100, 1000, 10000], _EVENT_BYTES
    ),
    "events.query": _Op(
        "events", "events", _setup_events_query, [100, 1000], [100, 1000, 10000], _EVENT_BYTES
    ),
    "channels.write": _Op(
        "channels",
        "samples",
        _setup_channels_write,
        [1, 1000],
        [1, 100, 1000, 10000],
        _SCALAR_BYTES,
    ),
    "channels.block": _Op(
        "channels",
        "points",
        _setup_channels_block,
        [1000, 10000],
        [1000, 10000, 100000],
        _SCALAR_BYTES,
    ),
    "channels.query": _Op(
        "channels", "samples", _setup_channels_query, [100, 1000], [100, 1000, 10000], _SCALAR_BYTES
    ),
    "channels.stream": _Op(
        "channels",
        "samples",
        _setup_channels_stream,
        [1, 1000],
        [1, 100, 1000, 10000],
        _SCALAR_BYTES,
    ),
    "runs.save": _Op("runs", "runs", _setup_runs_save, [1, 10], [1, 10, 100], _RUN_BYTES),
    "runs.list": _Op("runs", "runs", _setup_runs_list, [10, 100], [10, 100, 1000], _ROW_BYTES),
    "runs.steps": _Op("runs", "runs", _setup_runs_steps, [10, 100], [10, 100, 1000], _ROW_BYTES),
    "files.write": _Op("files", "artifacts", _setup_files_write, [1, 10], [1, 10, 100], 100 * 1024),
    "files.resolve": _Op("files", "files", _setup_files_resolve, [1], [1], _ROW_BYTES),
    "files.list": _Op("files", "files", _setup_files_list, [10, 100], [10, 100, 1000], _ROW_BYTES),
    "files.stream_raw": _Op(
        "files", "chunks", _setup_files_stream_raw, [1, 64], [1, 64, 256], 64 * 1024
    ),
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
    for op, spec in _OPS.items():
        scales = spec.full_scales if full else spec.fast_scales
        for scale in scales:
            cases.append(
                Workload(
                    op,
                    spec.store,
                    spec.unit,
                    scale,
                    writers=1,
                    setup=spec.factory(scale),
                    tier=tier,
                    bytes_per_unit=spec.bytes_per_unit,
                )
            )
    for op, rep_scale in _CONCURRENCY.items():
        spec = _OPS[op]
        for w in writer_counts:
            cases.append(
                Workload(
                    op,
                    spec.store,
                    spec.unit,
                    rep_scale,
                    writers=w,
                    setup=None,
                    tier=tier,
                    bytes_per_unit=spec.bytes_per_unit,
                )
            )
    return cases
