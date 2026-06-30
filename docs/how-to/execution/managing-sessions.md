# Managing Sessions

Open a session to use instruments outside pytest — in a script, a notebook, or the operator UI. This guide shows how to open a session, query it, and prune old session data. For what a session is, see [Sessions](../../concepts/data/sessions.md).

## Starting a Session

### With `connect()` (scripts, notebooks)

```python
from litmus import connect

with connect("cell-7") as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
    print(f"Session: {station.session_id}")
# Leaving the with-block closes the session and disconnects instruments
```

Leaving the `with` block ends the session for you. In a notebook where a `with` block is awkward, call `station.start()` after `connect(...)` and `station.stop()` when you're done.

### With pytest

Sessions are created automatically by the Litmus pytest plugin. Each test run gets a session with full context (station, UUT, operator).

## Session Metadata

Every session records the station, operator, and config it ran under (see [event types](../../reference/data/event-types.md) for the fields). Query it to answer questions like:

- What station was used?
- Who was the operator?
- What firmware was on the UUT?
- What was the station config at that time?

## Querying Sessions

### MCP Tool

```
# List all sessions
litmus_sessions()

# Get events for a specific session
litmus_events(session_id="abc12345-...")
```

### HTTP API

```bash
# List sessions
curl http://localhost:8000/api/sessions

# Session detail (all events)
curl http://localhost:8000/api/sessions/abc12345-1234-5678-abcd-1234567890ab
```

### Python

```python
from litmus.queries import EventStore

store = EventStore()
try:
    # All sessions (returns SessionStarted event dicts)
    # SessionStarted carries session/station/operator fields only — UUT lives on RunStarted.
    sessions = store.sessions()
    for s in sessions:
        print(f"{s['station_id']} - {s.get('operator_id')} - {s['occurred_at']}")

    # Events for one session — take an id from the list above
    events = store.events(session_id=sessions[0]["session_id"])
finally:
    store.close()
```

To get UUT serials, query `RunStarted` events for the session:

```python
runs = store.events(session_id=session_id, event_type="run.started")
for r in runs:
    print(f"{r['uut_serial_number']} ({r.get('uut_part_number')})")
```

## Data Retention

Session data is stored in date-partitioned directories under `<data_dir>/events/`. Nothing is deleted automatically — Litmus keeps everything until you prune it. Use `litmus data prune`:

```bash
# Preview what a 90-day cutoff would delete — nothing is removed
litmus data prune --older-than 90d --dry-run

# Prune data older than 90 days (run the preview first — this is permanent)
litmus data prune --older-than 90d
```

Pass `--data-types` to limit the prune to specific stores. Without `--dry-run`, the prune is permanent.

## See also
- [Sessions Concept](../../concepts/data/sessions.md) — Why sessions exist
- [connect() reference](../../reference/runtime/connect.md) — full API surface
- [Querying Events](../data/querying-events.md) — Event query patterns
