# Query Historical Events

Three ways to query events: MCP tool (AI agents), HTTP API (any client), or Python (in-process).

## MCP Tool: `litmus_events`

```
# All events for a session
litmus_events(session_id="abc12345-...")

# Only measurement events
litmus_events(event_type="test.measurement")

# Instrument reads for the DMM
litmus_events(event_type="instrument.read", role="dmm")

# Events since a timestamp
litmus_events(since="2026-03-10T14:00:00")

# Combine filters
litmus_events(session_id="abc...", event_type="test.step_ended", limit=50)
```

## HTTP API

```bash
# All events (default limit 100)
curl http://localhost:8000/api/events

# Filter by session
curl "http://localhost:8000/api/events?session_id=abc12345-..."

# Filter by type and role
curl "http://localhost:8000/api/events?type=instrument.read&role=dmm"

# Events after a time
curl "http://localhost:8000/api/events?since=2026-03-10T14:00:00&limit=50"
```

## Python: `EventStore`

```python
from uuid import UUID
from datetime import datetime
from litmus.data.event_store import EventStore

store = EventStore()
try:
    # All events for a session
    events = store.events(session_id=UUID("abc12345-..."))

    # Filter by type
    measurements = store.events(event_type="test.measurement")

    # Filter by role
    dmm_reads = store.events(role="dmm")

    # Events since a time
    recent = store.events(since=datetime(2026, 3, 10, 14, 0))

    # List sessions
    sessions = store.sessions()
finally:
    store.close()
```

## Filtering Options

| Filter | Description | Example Values |
|--------|-------------|----------------|
| `session_id` | UUID of the session | `"abc12345-1234-..."` |
| `event_type` | Dotted event type string | `"test.measurement"`, `"instrument.read"`, `"session.started"` |
| `role` | Instrument role name | `"dmm"`, `"psu"`, `"scope"` |
| `since` | ISO timestamp | `"2026-03-10T14:00:00"` |
| `limit` | Max results (HTTP/MCP only) | `100` |

Role filtering checks the `role`, `instrument_role`, and `channel_id` prefix fields across all event types.

## See Also

- [Event Types Reference](../reference/event-types.md) — All event type fields
- [Event Log Architecture](../concepts/event-log.md) — How events are stored
- [Subscribing to Events](querying-events.md) — Real-time monitoring
