"""Write-path resilience for the events store (#242).

Reads already self-heal across a daemon restart (``FlightQueryClient``
reacquires a fresh daemon and re-runs). Writes must too: if the daemon is
killed mid-stream (e.g. an upgrade where a newer client restarts an older
daemon), ``FlightPutStream`` reacquires and **resends** the un-acked
batches. The resend is safe because the events insert is
``ON CONFLICT (id) DO NOTHING`` — rows the dead daemon already committed
are no-ops.

Isolation: this uses its own tmp events daemon (like
``test_fresh_daemon_spawns_within_timeout``) so the kill never disturbs the
canonical daemon other tests share. It pushes hand-built batches via
``FlightPutStream`` directly — **no IPC files are written** — so the
post-kill batch can reach the index ONLY via resend, not via the daemon's
startup IPC sweep. That isolates the fix.
"""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data import duckdb_manager
from litmus.data._duckdb_flight_server import FlightPutStream
from litmus.data.events import TYPED_PAYLOAD_COLUMNS


def _events_batch(event_id: str, session_id: str) -> pa.RecordBatch:
    """Minimal events batch carrying exactly the columns the insert selects."""
    cols: dict[str, pa.Array] = {
        "id": pa.array([event_id], pa.string()),
        "event_type": pa.array(["test.write_resilience"], pa.string()),
        "occurred_at": pa.array([datetime.now(UTC)], pa.timestamp("us", tz="UTC")),
        "session_id": pa.array([session_id], pa.string()),
        "run_id": pa.array([None], pa.string()),
        "writer_key": pa.array(["w0"], pa.string()),
        "event_offset": pa.array([0], pa.int64()),
        "json": pa.array(["{}"], pa.string()),
    }
    for col in TYPED_PAYLOAD_COLUMNS:
        cols[col] = pa.array([None], pa.string())
    return pa.record_batch(cols)


def _query_event_ids(location: str) -> set[str]:
    client = flight.connect(location)
    try:
        ticket = flight.Ticket(b"events\0SELECT id FROM events")
        table = client.do_get(ticket).read_all()
        return {row["id"] for row in table.to_pylist()}
    finally:
        client.close()


def _kill_daemon(events_dir: Path) -> None:
    """Hard-kill the events daemon and reap it so ``acquire`` respawns.

    The daemon is a subprocess child of this test process, so after the
    kill it lingers as a zombie until reaped — and a zombie still answers
    ``os.kill(pid, 0)``, which would fool ``_pid_alive`` into thinking the
    daemon is up (in production the killed daemon isn't the killer's child,
    so there's no zombie). ``os.waitpid`` reaps it; then ``acquire`` sees
    the pid gone and spawns fresh.
    """
    pid = json.loads((events_dir / "_duckdb.json").read_text())["pid"]
    os.kill(pid, signal.SIGKILL)
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass  # not our child / already reaped


def test_write_path_resends_after_daemon_kill(tmp_path: Path) -> None:
    """A daemon kill mid-write must not lose writes — the stream reacquires
    a fresh daemon and resends, so the post-kill batch is queryable."""
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    sid = str(uuid4())

    location = duckdb_manager.acquire(events_dir)
    put = FlightPutStream(
        location,
        "events",
        "events",
        reacquire=lambda: duckdb_manager.acquire(events_dir),
    )
    try:
        # Pre-kill write lands in the (persistent) index.
        put.write(_events_batch("evt-before", sid))
        put.drain()
        assert _query_event_ids(location) == {"evt-before"}

        # Kill the daemon out from under the open stream.
        _kill_daemon(events_dir)

        # Post-kill write must self-heal: reacquire a fresh daemon + resend.
        # No IPC was written, so the ONLY way evt-after reaches the index is
        # the resend — not the daemon's startup sweep.
        put.write(_events_batch("evt-after", sid))
        put.drain()

        new_location = duckdb_manager.acquire(events_dir)
        ids = _query_event_ids(new_location)
        assert "evt-after" in ids, "post-kill write lost — resend did not recover it"
        # The committed pre-kill row survived the respawn (persistent index).
        assert "evt-before" in ids
    finally:
        # Hard-kill the (respawned) isolated daemon rather than release():
        # the test acquires several times and balancing every ref is brittle;
        # an unbalanced refcount would leave the daemon running for the whole
        # session (~100 threads), starving later daemon spawns. The tmp dir
        # is discarded, so killing outright is clean.
        put.close()
        try:
            _kill_daemon(events_dir)
        except (FileNotFoundError, KeyError, ProcessLookupError):
            pass
