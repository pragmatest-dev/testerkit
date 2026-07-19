# Query Historical Events

Three ways to query events: MCP tool (AI agents), HTTP API (any client), or Python (in-process). Most filters take a `session_id` — list recent sessions with `testerkit runs` or `store.sessions()` and copy one.

> **Prerequisites.** Every TesterKit test run writes events automatically; an empty store returns an empty list, not an error. The HTTP path needs `testerkit serve` running; the Python path needs only `testerkit` installed. Run `testerkit daemon status` to confirm which data dir is active.

## MCP Tool: `testerkit_events`

```
# All events for a session
testerkit_events(session_id="<session-id>")

# Only measurement events
testerkit_events(event_type="test.measurement")

# Channel events for the DMM (instrument reads land in the channel store)
testerkit_events(event_type="channel.started", role="dmm")

# Events at or after a timestamp (`since` is inclusive; UTC, ISO 8601)
testerkit_events(since="2026-03-10T14:00:00Z")

# Combine filters
testerkit_events(session_id="<session-id>", event_type="test.step_ended", limit=50)
```

## HTTP API

```bash
# All events (default limit 100)
curl http://localhost:8000/api/events

# Filter by session
curl "http://localhost:8000/api/events?session_id=<session-id>"

# Filter by type and role
curl "http://localhost:8000/api/events?type=channel.started&role=dmm"

# Events at or after a time (`since` is inclusive; UTC)
curl "http://localhost:8000/api/events?since=2026-03-10T14:00:00Z&limit=50"
```

## Python: `EventStore`

```python
from datetime import datetime, timezone
from testerkit.queries import EventStore

store = EventStore()
try:
    # List recent sessions and take one id to drill into
    sessions = store.sessions()
    sid = sessions[0]["session_id"] if sessions else None

    # All events for that session
    events = store.events(session_id=sid)

    # Filter by type
    measurements = store.events(event_type="test.measurement")

    # Filter by role
    dmm_reads = store.events(role="dmm")

    # Events at or after a time (`since` is inclusive; pass UTC-aware datetimes —
    # naive datetimes compare incorrectly against the stored UTC timestamps).
    recent = store.events(since=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc))
finally:
    store.close()
```

## Filtering Options

| Filter | Description | Example Values |
|--------|-------------|----------------|
| `session_id` | id of the session (from `testerkit runs`) | `"<session-id>"` |
| `event_type` | Dotted event type string | `"test.measurement"`, `"channel.started"`, `"session.started"` |
| `role` | Instrument role name | `"dmm"`, `"psu"`, `"scope"` |
| `since` | ISO timestamp (UTC). Inclusive — `received_at >= since`. | `"2026-03-10T14:00:00Z"` |
| `limit` | Max results | `100` (default for HTTP / MCP; Python defaults to no cap) |

`role` matches the instrument role on any event that carries one — e.g. `dmm`, `psu`, `scope`.

## See also
- [Event Types Reference](../../reference/data/event-types.md) — All event type fields
- [Event Log Architecture](../../concepts/data/event-log.md) — How events are stored
- [MCP integration](../overview/mcp-integration.md) — Setting up `testerkit_events` and the other MCP tools for AI clients
