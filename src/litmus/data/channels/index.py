"""Session-less channel index — the warm DuckDB reader behind ChannelStore.

A channel producer *stamps* its ``session_id`` onto every sample it writes; this
index *reads* session_ids off the rows and has none of its own. It is the
cross-session corpus: a persistent derived cache (``_index.duckdb`` in the
channels dir) over the producer IPC segments, plus an ephemeral ``:memory:``
overlay for live ``do_put`` rows. Producer IPC files remain the durable truth;
the index survives a daemon restart and is brought current by an incremental
ledger-gated scan.

``ChannelStore`` composes one of these when indexing is enabled — on the daemon
(it ingests + serves at-rest query) and in warm-index tests (a writer that also
indexes its own writes). The index never knows which session is "active": every
read filters by a ``session_id`` query parameter, never an instance field.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc

from litmus.data.channels.models import (
    ChannelDescriptor,
    ChannelSample,
    encode_value,
)
from litmus.data.schema_dispatch import (
    SchemaVersionRefused,
    dispatch,
    stamp_from_arrow_metadata,
)
from litmus.data.schema_versions import SchemaStore


def _to_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to UTC (or pass through ``None``).

    Naive datetimes are interpreted as already UTC; aware datetimes
    are converted via ``astimezone(UTC)``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _lttb_indices(values: Sequence[float], n_out: int) -> list[int]:
    """Largest Triangle Three Buckets downsampling — return selected indices.

    Visually lossless: preserves peaks, valleys, and shape better than naive
    stride decimation. Delegates to ``tsdownsample`` (compiled LTTB); first and
    last points are always kept.

    Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
    Representation", MSc thesis, University of Iceland, 2013.
    """
    n = len(values)
    if n <= n_out or n_out < 3:
        return list(range(n))
    # Heavy deps deferred off the module import path — only the decimation
    # (query w/ max_points) path pays numpy/tsdownsample's load.
    import numpy as np  # noqa: PLC0415
    from tsdownsample import LTTBDownsampler  # noqa: PLC0415

    indices = LTTBDownsampler().downsample(np.asarray(values, dtype=float), n_out=n_out)
    return [int(i) for i in indices]


def _decimate_table(table: pa.Table, max_points: int) -> pa.Table:
    """Apply LTTB decimation to an Arrow table.

    Uses the ``value`` column for scalar channels, or row index for
    struct/array channels (where there's no single numeric column).
    """
    n = len(table)
    if n <= max_points:
        return table

    # Find best column for LTTB area calculation
    if "value" in table.schema.names:
        col = table.column("value")
        try:
            values = [float(v.as_py()) for v in col]
        except (TypeError, ValueError):
            # Non-numeric value column — fall back to stride
            indices = list(range(0, n, max(1, n // max_points)))[:max_points]
            return table.take(indices)
    else:
        # Struct/array channel — use row index as proxy (preserves time density)
        values = list(range(n))

    indices = _lttb_indices(values, max_points)
    return table.take(indices)


class ChannelIndex:
    """Warm DuckDB index over closed channel segments + a live overlay.

    Session-less: reads session_ids off the data rows and filters by a query
    parameter. Owns the index connection, the incremental scan ledger, the
    per-(channel, session) registry, and the live-row pending buffer.
    """

    _INDEX_ENVELOPE = frozenset(
        {
            "received_at",
            "sampled_at",
            "source_method",
            "session_id",
            "sample_interval",
            "sample_offset",
        }
    )

    _INDEX_ARROW_SCHEMA = pa.schema(
        [
            ("channel_id", pa.utf8()),
            ("session_id", pa.utf8()),
            ("received_at", pa.timestamp("us", tz="UTC")),
            ("sampled_at", pa.timestamp("us", tz="UTC")),
            ("source_method", pa.utf8()),
            ("sample_interval", pa.float64()),
            ("value", pa.utf8()),
            ("sample_offset", pa.int64()),
        ]
    )

    # Bound how often a query re-globs the channels dir for newly-closed
    # segments — frequent dashboard polls shouldn't each pay a directory walk.
    _RUNTIME_SCAN_INTERVAL = 1.0

    _REGISTRY_COLUMNS = (
        "channel_id, session_id, hostname, value_type, instrument_role, "
        "resource, unit, first_seen, last_updated"
    )

    def __init__(self, channels_dir: Path) -> None:
        self._channels_dir = channels_dir
        # Served descriptor cache (channel-keyed, first-wins) populated by
        # absorb_descriptor as segments/streams are read.
        self._descriptors: dict[str, ChannelDescriptor] = {}
        self._index_db: duckdb.DuckDBPyConnection | None = None
        self._index_local = threading.local()
        self._index_lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._pending_threshold = 100
        self._scan_lock = threading.Lock()
        self._last_scan = 0.0

    # ---- descriptor cache ----

    def has(self, channel_id: str) -> bool:
        """Whether a descriptor has been absorbed for this channel."""
        return channel_id in self._descriptors

    def descriptor(self, channel_id: str) -> ChannelDescriptor | None:
        """The absorbed descriptor for a channel, or None."""
        return self._descriptors.get(channel_id)

    def descriptors(self) -> list[ChannelDescriptor]:
        """All absorbed descriptors (one per channel)."""
        return list(self._descriptors.values())

    # ---- lifecycle ----

    def open(self) -> None:
        """Open the on-disk index and fold in segments closed since last run.

        The index is a persistent derived cache (``_index.duckdb`` in the
        channels dir): it survives a daemon restart and is brought current
        by an **incremental** scan — only segments not already in the
        ``_ingested`` ledger are read (vs. the old wipe-and-rebuild-from-all
        on every start). Producer IPC files remain the durable truth.

        Live ``do_put`` rows ride a separate attached ``:memory:`` overlay
        (``live.channel_live``): they are ephemeral (lost on restart, then
        re-derived from their now-closed segments by the incremental scan),
        so they never collide with a segment-scanned row. Mirrors the runs
        daemon's persistent-index + in-memory-overlay split.
        """
        index_path = self._channels_dir / "_index.duckdb"
        self._index_db = duckdb.connect(str(index_path))
        self._ensure_schema(self._index_db)
        # Ephemeral live overlay: attached :memory: so it's visible to every
        # child read cursor (a register()'d temp view would not be), yet not
        # persisted — it's a projection of in-flight samples, re-derived from
        # segments on restart.
        self._index_db.execute("ATTACH ':memory:' AS live")
        self._index_db.execute(
            "CREATE TABLE live.channel_live AS SELECT * FROM channel_index LIMIT 0"
        )
        self._scan_disk()

    def close(self) -> None:
        """Close the index connection."""
        if self._index_db is not None:
            try:
                self._index_db.close()
            except (OSError, duckdb.Error):
                pass
            self._index_db = None

    @staticmethod
    def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
        """Create the on-disk index schema if absent.

        The index is a disposable projection — every row is re-derivable from
        the durable ``.arrow`` segments. ``CREATE TABLE IF NOT EXISTS`` keeps an
        existing table as-is, so a column change is not an in-place migration:
        clear ``data/channels`` and let the next open rebuild the projection.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_index (
                channel_id VARCHAR,
                session_id VARCHAR,
                received_at TIMESTAMPTZ,
                sampled_at TIMESTAMPTZ,
                source_method VARCHAR,
                sample_interval DOUBLE,
                value VARCHAR,
                sample_offset BIGINT
            )
            """
        )
        # Ledger of ingested segments — keyed on path alone. A channel
        # segment is written exactly once (one batch, then closed
        # immutable), so a path that's already recorded never needs
        # re-reading. (Events key on (path, mtime, size) because its IPC
        # files grow; channel segments don't.)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _ingested (path VARCHAR PRIMARY KEY, row_count BIGINT)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_index_cid_recv "
            "ON channel_index(channel_id, received_at)"
        )
        # Derived registry: one version row per (channel, session). Non-unique on
        # (hostname, channel) — each session that opens a channel appends a row, so
        # current def = latest, history = all rows. ``last_updated`` (coarse) is the
        # freshest received_at seen, for liveness staleness.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_registry (
                channel_id VARCHAR,
                session_id VARCHAR,
                hostname VARCHAR,
                value_type VARCHAR,
                instrument_role VARCHAR,
                resource VARCHAR,
                unit VARCHAR,
                first_seen TIMESTAMPTZ,
                last_updated TIMESTAMPTZ,
                PRIMARY KEY (channel_id, session_id)
            )
            """
        )

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        """Thread-local read cursor over the shared in-memory index."""
        cur = getattr(self._index_local, "cur", None)
        if cur is None:
            assert self._index_db is not None
            cur = self._index_db.cursor()
            self._index_local.cur = cur
        return cur

    # ---- segment scanning ----

    def _scan_disk(self) -> None:
        """Fold segments closed since last run into the persistent index.

        Incremental: a segment already in the ``_ingested`` ledger is
        skipped, so a daemon restart only reads new files rather than
        rebuilding the whole index from every segment.
        """
        if self._index_db is None:
            return
        pattern = re.compile(r"^(.+)_([0-9a-f]{8})(?:_\d+)?$")
        with self._index_lock:
            ingested = {
                row[0] for row in self._index_db.execute("SELECT path FROM _ingested").fetchall()
            }
        for arrow_file in sorted(self._channels_dir.glob("*/*.arrow")):
            path_str = str(arrow_file)
            if path_str in ingested:
                continue
            m = pattern.match(arrow_file.stem)
            if not m:
                continue
            try:
                reader = ipc.open_stream(pa.OSFile(str(arrow_file), "rb"))
                table = reader.read_all()
            except (pa.ArrowInvalid, OSError):
                # Torn / still-open segment — leave it out of the ledger so
                # the next restart re-reads it once it's a complete file.
                continue
            try:
                adapter = dispatch(
                    SchemaStore.CHANNELS, stamp_from_arrow_metadata(table.schema.metadata)
                )
            except SchemaVersionRefused:
                # Unsupported version — skip WITHOUT ledgering, so the segment is
                # re-read on the next scan and a newer daemon that knows the
                # version ingests it instead of losing it (#43). The presence-only
                # ledger can't mark a permanent quarantine, so this covers
                # deferrable + absent alike — consistent with runs/events leaving
                # deferrable refusals un-ledgered.
                continue
            table = adapter(table)
            desc = self.absorb_descriptor(m.group(1), table.schema)
            if desc is not None and "received_at" in table.column_names:
                recv = [t for t in table.column("received_at").to_pylist() if t is not None]
                if recv:
                    self.bump_last_updated(desc.channel_id, desc.session_id, max(recv))
            rows = self._segment_rows_to_index(m.group(1), table)
            self._insert_index_rows(rows, "channel_index", ledger_path=path_str)

    def _maybe_scan_disk(self) -> None:
        """Fold newly-closed segments into the index before a query (throttled).

        Closes the restart-recovery gap: a sample the live push dropped under
        overflow is durable in its closed segment and becomes queryable here,
        without bouncing the daemon. Incremental (ledger-gated) and rate-limited;
        the union dedup in ``query`` absorbs any overlap with the overlay.
        """
        now = time.monotonic()
        with self._scan_lock:
            if now - self._last_scan < self._RUNTIME_SCAN_INTERVAL:
                return
            self._last_scan = now
        self._scan_disk()

    # ---- registry ----

    def absorb_descriptor(self, channel_id: str, schema: pa.Schema) -> ChannelDescriptor | None:
        """Populate the served descriptor map + registry from segment/stream metadata.

        Producers stamp the ``ChannelDescriptor`` as Arrow schema metadata on
        every segment; the daemon (which never calls ``write()``) reads it here
        so ``list_channel_info`` can serve it. Returns the parsed descriptor (or
        ``None`` if absent) so callers can reuse it without re-parsing.
        """
        meta = (schema.metadata or {}).get(b"litmus.channel_descriptor")
        if not meta:
            return None
        desc = ChannelDescriptor.model_validate_json(meta)
        # Registry row is per (channel, session): establish it even when the
        # channel-keyed descriptor cache already holds another session's descriptor
        # (its last-write-wins would otherwise drop this session's version row).
        self._register_descriptor_row(desc)
        if channel_id not in self._descriptors:
            self._descriptors[channel_id] = desc
        return desc

    def _register_descriptor_row(self, desc: ChannelDescriptor) -> None:
        """Establish a (channel, session) registry version row (idempotent).

        Carries the per-session hostname/descriptor; ``last_updated`` starts NULL
        and is advanced by :meth:`bump_last_updated`. No-op off the indexed daemon.
        """
        if self._index_db is None:
            return
        with self._index_lock:
            self._index_db.execute(
                """
                INSERT INTO channel_registry
                    (channel_id, session_id, hostname, value_type, instrument_role,
                     resource, unit, first_seen, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT (channel_id, session_id) DO NOTHING
                """,
                [
                    desc.channel_id,
                    desc.session_id,
                    desc.hostname,
                    desc.value_type,
                    desc.instrument_role,
                    desc.resource,
                    desc.unit,
                    desc.first_seen,
                ],
            )

    def bump_last_updated(self, channel_id: str, session_id: str, ts: datetime | None) -> None:
        """Advance a registry row's ``last_updated`` to ``ts`` if newer (NULL-safe)."""
        if self._index_db is None or ts is None or not session_id:
            return
        with self._index_lock:
            self._index_db.execute(
                """
                UPDATE channel_registry
                SET last_updated = CASE
                    WHEN last_updated IS NULL OR ? > last_updated THEN ? ELSE last_updated END
                WHERE channel_id = ? AND session_id = ?
                """,
                [ts, ts, channel_id, session_id],
            )

    def query_registry(self) -> pa.Table:
        """All ``(hostname, channel, session)`` registry version rows.

        Daemon-side read of the derived registry (folds any newly-closed segments
        first). Off the indexed daemon this is empty.
        """
        if self._index_db is None:
            return pa.table({c.strip(): [] for c in self._REGISTRY_COLUMNS.split(",")})
        self._maybe_scan_disk()
        cur = self._cursor()
        result = cur.execute(f"SELECT {self._REGISTRY_COLUMNS} FROM channel_registry")
        return result.arrow().read_all()

    # ---- row building + insert ----

    @classmethod
    def _segment_rows_to_index(cls, channel_id: str, table: pa.Table) -> list[dict[str, Any]]:
        """Convert a typed segment table to index rows (``value`` JSON-encoded).

        ``channel_id`` comes from the filename (segments don't store it);
        ``session_id`` from the row column. Scalar/array rows carry a
        ``value`` column; dict/struct rows fold their non-envelope columns
        back into one JSON object.
        """
        out: list[dict[str, Any]] = []
        for r in table.to_pylist():
            if "value" in r:
                payload = r["value"]
            else:
                payload = {k: v for k, v in r.items() if k not in cls._INDEX_ENVELOPE}
            out.append(
                {
                    "channel_id": channel_id,
                    "session_id": r.get("session_id"),
                    "received_at": r.get("received_at"),
                    "sampled_at": r.get("sampled_at"),
                    "source_method": r.get("source_method") or "",
                    "sample_interval": r.get("sample_interval"),
                    "value": encode_value(payload),
                    # Carry the segment's sample_offset into the index so a scanned row
                    # dedups against the same sample in the live overlay (else the
                    # runtime fold would double-count it with a null sample_offset).
                    "sample_offset": r.get("sample_offset", -1),
                }
            )
        return out

    def _insert_index_rows(
        self,
        rows: list[dict[str, Any]],
        table: str,
        *,
        ledger_path: str | None = None,
    ) -> None:
        """Insert index rows under the write lock (single writer).

        ``table`` is ``channel_index`` (durable, segment-scanned) or
        ``live.channel_live`` (ephemeral overlay). ``ledger_path``, when
        given, records the source segment in ``_ingested`` in the SAME
        transaction as the insert, so a crash can't half-record a segment.
        """
        if self._index_db is None:
            return
        if not rows:
            # An empty segment still needs its ledger mark so it isn't
            # re-read on every restart.
            if ledger_path is not None:
                with self._index_lock:
                    self._index_db.execute(
                        "INSERT OR IGNORE INTO _ingested (path, row_count) VALUES (?, 0)",
                        [ledger_path],
                    )
            return
        tbl = pa.Table.from_pylist(rows, schema=self._INDEX_ARROW_SCHEMA)
        with self._index_lock:
            self._index_db.register("_incoming", tbl)
            self._index_db.execute(f"INSERT INTO {table} SELECT * FROM _incoming")
            self._index_db.unregister("_incoming")
            if ledger_path is not None:
                self._index_db.execute(
                    "INSERT OR IGNORE INTO _ingested (path, row_count) VALUES (?, ?)",
                    [ledger_path, len(rows)],
                )

    def insert_live_columnar(self, src_batch: pa.RecordBatch) -> None:
        """Columnar index insert for the scalar fast path: project the wire batch's
        columns straight into the index schema and INSERT…SELECT — no per-row
        dict build. The wire ``value`` (JSON utf8) is already the index encoding."""
        if self._index_db is None or src_batch.num_rows == 0:
            return
        idx = pa.record_batch(
            {name: src_batch.column(name) for name in self._INDEX_ARROW_SCHEMA.names},
            schema=self._INDEX_ARROW_SCHEMA,
        )
        tbl = pa.Table.from_batches([idx])
        with self._index_lock:
            self._index_db.register("_incoming", tbl)
            self._index_db.execute("INSERT INTO live.channel_live SELECT * FROM _incoming")
            self._index_db.unregister("_incoming")

    @staticmethod
    def _payload_and_interval(sample: ChannelSample) -> tuple[Any, float | None]:
        """Split an array sample's ``{value, sample_interval}`` envelope.

        ``_normalize_value`` folds arrays into ``{"value": [...],
        "sample_interval": dt}``, which is what rides on the live sample.
        Segments store the array in the ``value`` column with
        ``sample_interval`` alongside, so the index must too — otherwise a
        live-ingested array would encode differently from a disk-scanned one.
        """
        v = sample.value
        if (
            isinstance(v, dict)
            and set(v.keys()) == {"value", "sample_interval"}
            and isinstance(v.get("value"), list)
        ):
            return v["value"], v["sample_interval"]
        return v, sample.sample_interval

    def index_row(self, channel_id: str, sample: ChannelSample) -> dict[str, Any]:
        """Build one index row from a sample (``value`` JSON-encoded)."""
        payload, interval = self._payload_and_interval(sample)
        return {
            "channel_id": channel_id,
            "session_id": sample.session_id,
            "received_at": sample.received_at,
            "sampled_at": sample.sampled_at,
            "source_method": sample.source_method or "",
            "sample_interval": interval,
            "value": encode_value(payload),
            "sample_offset": sample.sample_offset,
        }

    def extend_pending(self, rows: list[dict[str, Any]]) -> None:
        """Append index rows to the pending buffer; flush past the threshold."""
        if not rows:
            return
        with self._pending_lock:
            self._pending.extend(rows)
            overflowed = len(self._pending) >= self._pending_threshold
        if overflowed:
            self._flush_pending()

    def _flush_pending(self) -> None:
        """Move pending live rows into the index."""
        with self._pending_lock:
            if not self._pending:
                return
            pending = self._pending
            self._pending = []
        # Live rows land in the ephemeral overlay, NOT the durable index:
        # their durable copy is the producer segment, folded into
        # channel_index by the incremental scan on the next restart.
        self._insert_index_rows(pending, "live.channel_live")

    # ---- query ----

    def query(
        self,
        channel_id: str,
        *,
        session_id: str | None,
        start: datetime | None,
        end: datetime | None,
        last_n: int | None,
        max_points: int | None,
        sample_offset: int | None = None,
    ) -> pa.Table:
        """At-rest query served from the warm index (∪ pending buffer)."""
        self._maybe_scan_disk()
        self._flush_pending()
        cur = self._cursor()
        # Union the durable index with the live overlay (same columns). A sample
        # lands in the overlay (push) and/or channel_index (segment scan), and the
        # runtime fold can put it in both — _dedup_on_sample_offset below collapses the
        # overlap on the per-sample cursor (session, sample_offset).
        sql = [
            "SELECT received_at, sampled_at, value, source_method, "
            "session_id, sample_interval, sample_offset FROM ("
            "SELECT * FROM channel_index UNION ALL SELECT * FROM live.channel_live"
            ") WHERE channel_id = ?"
        ]
        params: list[Any] = [channel_id]
        if session_id:
            sql.append("AND left(session_id, 8) = left(?, 8)")
            params.append(session_id)
        start_utc = _to_utc(start)
        end_utc = _to_utc(end)
        if start_utc is not None:
            sql.append("AND received_at >= ?")
            params.append(start_utc)
        if end_utc is not None:
            sql.append("AND received_at <= ?")
            params.append(end_utc)
        if sample_offset is not None:
            sql.append("AND sample_offset = ?")
            params.append(sample_offset)
        sql.append("ORDER BY received_at")
        table = self._dedup_on_sample_offset(cur.execute(" ".join(sql), params).arrow().read_all())

        if last_n is not None and table.num_rows > last_n:
            table = table.slice(table.num_rows - last_n)
        table = self._decode_value_column(table)
        if max_points is not None and table.num_rows > max_points:
            table = _decimate_table(table, max_points)
        return table

    @staticmethod
    def _dedup_on_sample_offset(table: pa.Table) -> pa.Table:
        """Collapse overlay∪index overlap on the per-sample cursor (session, sample_offset).

        The runtime segment fold can place a sample in both the durable index and
        the live overlay; both copies are identical, so keep the first (the table
        is already ordered by ``received_at``). A no-op when they don't overlap.
        Rows with an unstamped ``sample_offset`` (< 0, legacy) are never collapsed.
        """
        if table.num_rows == 0 or "sample_offset" not in table.column_names:
            return table
        sessions = table.column("session_id").to_pylist()
        sample_offsets = table.column("sample_offset").to_pylist()
        seen: set[tuple[Any, int]] = set()
        keep: list[int] = []
        for i, (s, o) in enumerate(zip(sessions, sample_offsets, strict=True)):
            if o is not None and o >= 0:
                key = (s, o)
                if key in seen:
                    continue
                seen.add(key)
            keep.append(i)
        return table if len(keep) == table.num_rows else table.take(keep)

    @staticmethod
    def _decode_value_column(table: pa.Table) -> pa.Table:
        """JSON-decode the VARCHAR ``value`` column back to typed values.

        Inverse of ``encode_value``: non-JSON strings pass through (matches
        ``batch_row_to_sample``). Values within one channel are homogeneous,
        so Arrow infers a single column type.
        """
        if "value" not in table.column_names or table.num_rows == 0:
            return table
        decoded: list[Any] = []
        for v in table.column("value").to_pylist():
            if v is None:
                decoded.append(None)
                continue
            try:
                decoded.append(json.loads(v))
            except (json.JSONDecodeError, TypeError):
                decoded.append(v)
        idx = table.column_names.index("value")
        return table.set_column(idx, "value", pa.array(decoded))
