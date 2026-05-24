# Managing Sessions

Sessions track the connect-to-disconnect lifecycle of instrument usage. This guide covers creating, querying, and maintaining sessions.

## Starting a Session

### With `connect()` (scripts, notebooks)

```python
from litmus.connect import connect

with connect("cell-7") as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
    print(f"Session: {station.session_id}")
# SessionEnded emitted automatically
```

### With pytest

Sessions are created automatically by the Litmus pytest plugin. Each test run gets a session with full context (station, DUT, operator).

## Session Metadata

Every session captures rich context via the `SessionStarted` event (see [reference/event-types](../reference/event-types.md)). Query it to answer questions like:

- What station was used?
- Who was the operator?
- What firmware was on the DUT?
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
from litmus.data.event_store import EventStore

store = EventStore()
try:
    # All sessions (returns SessionStarted event dicts)
    # SessionStarted carries session/station/operator fields only — DUT lives on RunStarted.
    sessions = store.sessions()
    for s in sessions:
        print(f"{s['station_id']} - {s.get('operator_id')} - {s['occurred_at']}")

    # Events for a session
    events = store.events(session_id=UUID("abc12345-..."))
finally:
    store.close()
```

To get DUT serials, query `RunStarted` events for the session:

```python
runs = store.events(session_id=session_id, event_type="run.started")
for r in runs:
    print(f"{r['dut_serial']} ({r.get('dut_part_number')})")
```

## Data Retention

Session data is stored in date-partitioned directories under `<data_dir>/events/`. Manage retention with:

```bash
# Prune old data (planned CLI command)
litmus data prune --older-than 90d
```

Data retention settings can be configured in the global config at `~/.config/litmus/config.yaml` or per-project in `litmus.yaml`.

Default: unlimited (keep everything). No surprise data loss.

## See Also

- [Sessions Concept](../concepts/data/sessions.md) — Why sessions exist
- [connect() reference](../reference/connect.md) — full API surface
- [Querying Events](querying-events.md) — Event query patterns
