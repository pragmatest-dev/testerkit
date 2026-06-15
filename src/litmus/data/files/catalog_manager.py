"""Files catalog daemon manager + client helpers.

Subclasses ``DaemonManager`` for the files catalog DuckDB daemon and
provides the client-side query/push/discovery helpers consumers use to
reach the daemon's warm catalog (req 2) instead of walking the tree.

Blobs + ``.meta.json`` sidecars are the durable truth; the in-memory
catalog is rebuilt from them on every daemon start.
"""

from __future__ import annotations

import json
import threading
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data._daemon_lifecycle import DaemonManager, _pid_alive, wait_for_location
from litmus.data._flight_query import (
    FlightQueryClient,
    _drop_pooled_client,
    _get_pooled_client,
    call_options,
    probe_sql,
)
from litmus.data._push_relay import PushRelay
from litmus.data.files.catalog import (
    CATALOG_ARROW_SCHEMA,
    FRAME_ARROW_SCHEMA,
    FRAMES_DB,
)


class FilesCatalogManager(DaemonManager):
    """Manages the files catalog DuckDB daemon."""

    _state_name = "_files_catalog.json"
    _lock_name = "_files_catalog.lock"
    _ready_name = "_files_catalog_ready"
    _pid_name = "_files_catalog_pid"
    _daemon_module = "litmus.data.files._catalog_daemon"
    _port_file = "_files_catalog_flight_port"


def acquire(files_dir: Path) -> str:
    """Acquire a ref to the catalog daemon, starting it if needed.

    Returns the gRPC location string for Flight queries. Probes the daemon
    after acquiring: a wedged or dead Flight thread (PID alive but not
    responding) is killed and respawned so callers get a working connection.
    """
    mgr = FilesCatalogManager(files_dir)
    mgr.acquire()
    location = wait_for_location(mgr, files_dir, "files")
    if not probe_sql(location, "files"):
        warnings.warn(
            f"Files catalog daemon at {location} is not responding — killing and respawning.",
            stacklevel=2,
        )
        mgr.force_restart()
        mgr.acquire()
        location = wait_for_location(mgr, files_dir, "files")
    return location


def release(files_dir: Path) -> None:
    """Release our reference to the catalog daemon."""
    FilesCatalogManager(files_dir).release()


def is_running(files_dir: Path) -> bool:
    """Return True iff a live catalog daemon is serving ``files_dir``.

    Inspection only — never spawns. Consumers use this to prefer the
    warm catalog when a daemon is up, and fall back to the local walk
    otherwise (so unit tests with throwaway data dirs don't spawn a
    per-test daemon). Phase E removes the walk fallback entirely.
    """
    state = files_dir / FilesCatalogManager._state_name
    if not state.exists():
        return False
    try:
        data = json.loads(state.read_text())
        pid = data.get("pid")
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(pid, int) and _pid_alive(pid)


def _sql_str(value: str) -> str:
    return value.replace("'", "''")


def query_catalog(files_dir: Path, sql: str) -> list[dict[str, Any]]:
    """Run a catalog SQL query against the daemon (acquires + releases)."""
    location = acquire(files_dir)
    try:
        client = FlightQueryClient(
            location,
            "files",
            reacquire=lambda: acquire(files_dir),
            label="FileCatalog",
        )
        return client.query(sql)
    finally:
        release(files_dir)


def resolve_uri(files_dir: Path, uri: str) -> str | None:
    """Resolve a ``file://`` URI to its on-disk path via the warm catalog."""
    rows = query_catalog(
        files_dir,
        f"SELECT path FROM file_catalog WHERE uri = '{_sql_str(uri)}' LIMIT 1",
    )
    return rows[0]["path"] if rows else None


def list_recent(files_dir: Path, limit: int) -> list[dict[str, Any]]:
    """Return the ``limit`` most-recent catalog rows, newest first."""
    return query_catalog(
        files_dir,
        f"SELECT * FROM file_catalog ORDER BY created_at DESC LIMIT {int(limit)}",
    )


def list_artifacts(
    files_dir: Path,
    *,
    uri: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return catalog rows newest-first, optionally filtered.

    ``uri`` returns the single matching artifact; ``session_id`` /
    ``run_id`` filter the listing. SQL is built here, inside the files
    store, so callers (HTTP API, MCP tool) never touch the catalog
    table directly.
    """
    clauses = []
    if uri:
        clauses.append(f"uri = '{_sql_str(uri)}'")
    if session_id:
        clauses.append(f"session_id = '{_sql_str(session_id)}'")
    if run_id:
        clauses.append(f"run_id = '{_sql_str(run_id)}'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return query_catalog(
        files_dir,
        f"SELECT * FROM file_catalog {where} ORDER BY created_at DESC LIMIT {int(limit)}",
    )


def push_artifact(files_dir: Path, row: dict[str, Any]) -> None:
    """Push one catalog row to the daemon (best-effort, non-fatal).

    Called by ``FileStore.write`` after the blob + sidecar land durably.
    Non-fatal: the sidecar is the durable truth, so a failed push just
    means the artifact isn't in the warm catalog until the next daemon
    restart rescans it. Skips silently when no daemon is running, so
    plain writes never spawn one.
    """
    if not is_running(files_dir):
        return
    location = acquire(files_dir)
    try:
        client = _get_pooled_client(location)
        tbl = pa.Table.from_pylist([row], schema=CATALOG_ARROW_SCHEMA)
        descriptor = flight.FlightDescriptor.for_command(b"files\0file_catalog")
        writer, reader = client.do_put(descriptor, tbl.schema, options=call_options())
        writer.write_table(tbl)
        # Drain the server ACK(s) before returning: each ACK confirms the daemon
        # committed one batch, so a resolve_uri right after write() is guaranteed
        # to see the row (read-after-write) instead of racing the insert.
        for _ in tbl.to_batches():
            reader.read()
        writer.close()
    except (OSError, RuntimeError, pa.ArrowException) as exc:
        _drop_pooled_client(location)
        warnings.warn(f"Files catalog push failed (non-fatal): {exc}", stacklevel=2)
    finally:
        release(files_dir)


def publish_frame(
    files_dir: Path,
    *,
    stream_id: str,
    uri: str,
    byte_offset: int,
    length: int,
    payload: bytes | None = None,
) -> None:
    """Publish one ephemeral stream-frame (best-effort).

    Called by a streaming sink after each ``write``. Fans out to live
    subscribers push-style (req 5: no poll). The frame carries the chunk
    ``payload`` so a consumer receives the new bytes directly — never
    range-reading a still-growing object (an object-store backend can't
    serve one). raw/jsonl pass the bytes; format sinks (tdms/h5) pass
    ``None`` and a subscriber rejoins via a library reload at the next
    boundary. NOT persisted — the on-disk artifact is the durable record.
    Skips silently when no daemon runs, so plain streaming never spawns one.
    """
    if not is_running(files_dir):
        return
    location = acquire(files_dir)
    try:
        client = _get_pooled_client(location)
        tbl = pa.Table.from_pylist(
            [
                {
                    "stream_id": stream_id,
                    "uri": uri,
                    "byte_offset": byte_offset,
                    "length": length,
                    "payload": payload,
                }
            ],
            schema=FRAME_ARROW_SCHEMA,
        )
        descriptor = flight.FlightDescriptor.for_command(f"{FRAMES_DB}\0frames".encode())
        writer, _ = client.do_put(descriptor, tbl.schema, options=call_options())
        writer.write_table(tbl)
        writer.close()
    except (OSError, RuntimeError, pa.ArrowException) as exc:
        _drop_pooled_client(location)
        warnings.warn(f"Files frame publish failed (non-fatal): {exc}", stacklevel=2)
    finally:
        release(files_dir)


class _FrameTransport:
    """The files-frame codec + held transport behind a :class:`PushRelay`.

    Coalesces a drained burst of frame dicts into one ``RecordBatch`` and
    ``do_put``s it to the catalog daemon's hook-only frames db on a pooled,
    held client. On error it drops + reacquires the pooled client; the on-disk
    byte stream is the durable record, so a failed push is non-fatal.
    """

    _DESCRIPTOR = flight.FlightDescriptor.for_command(f"{FRAMES_DB}\0frames".encode())

    def __init__(self, location: str) -> None:
        self._location = location
        self._client = _get_pooled_client(location)

    def flush(self, _key: object, rows: list[dict[str, Any]]) -> None:
        try:
            tbl = pa.Table.from_pylist(rows, schema=FRAME_ARROW_SCHEMA)
            writer, _ = self._client.do_put(self._DESCRIPTOR, tbl.schema, options=call_options())
            writer.write_table(tbl)
            writer.close()
        except (OSError, RuntimeError, pa.ArrowException) as exc:
            _drop_pooled_client(self._location)
            self._client = _get_pooled_client(self._location)
            warnings.warn(f"Files frame relay do_put failed (non-fatal): {exc}", stacklevel=2)


def open_frame_relay(files_dir: Path) -> PushRelay | None:
    """Start a non-blocking frame relay iff a catalog daemon is already serving.

    Resolved ONCE per stream (not per chunk). Returns ``None`` when no daemon
    runs — the common no-subscriber / benchmark case — so the writer's hot path
    skips all frame work. Never spawns a daemon. The shared :class:`PushRelay`
    owns the queue + drain + drop-oldest overflow; :class:`_FrameTransport`
    supplies the frame codec + held ``do_put``.
    """
    if not is_running(files_dir):
        return None
    transport = _FrameTransport(acquire(files_dir))
    return PushRelay(
        flush=transport.flush,
        max_weight=256,  # frames coalesced per do_put
        max_wait=0.05,
        queue_max=1024,
        thread_name="files-frame-relay",
        on_close=lambda: release(files_dir),
    )


def subscribe_frames(
    files_dir: Path,
    callback: Callable[[dict[str, Any]], None],
) -> Callable[[], None]:
    """Subscribe to live stream-frame notifications. Returns an unsub callable.

    Spawns a reader thread that calls ``callback`` with each frame dict
    (``stream_id``/``uri``/``byte_offset``/``length``). Holds a daemon
    ref for the subscription's lifetime; ``unsub`` closes the stream and
    releases it. Uses a dedicated client so closing it cleanly interrupts
    the held-open ``do_get``.
    """
    location = acquire(files_dir)
    client = flight.connect(location)
    stop = threading.Event()

    def _run() -> None:
        try:
            reader = client.do_get(flight.Ticket(f"{FRAMES_DB}\0__SUBSCRIBE__".encode()))
            for chunk in reader:
                if stop.is_set():
                    break
                batch = chunk.data
                for i in range(batch.num_rows):
                    callback({c: batch.column(c)[i].as_py() for c in batch.schema.names})
        except (OSError, pa.ArrowException):
            pass

    thread = threading.Thread(target=_run, daemon=True, name="files-frame-sub")
    thread.start()

    def unsub() -> None:
        stop.set()
        try:
            client.close()
        except (OSError, RuntimeError, pa.ArrowException):
            pass
        release(files_dir)

    return unsub
