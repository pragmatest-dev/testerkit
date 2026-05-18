# Query Historical Events

Three ways to query events: MCP tool (AI agents), HTTP API (any client), or Python (in-process).

> **Prerequisites.** Events already written under `<data_dir>/events/` — every Litmus test run writes events automatically; empty stores return empty lists, not errors. `<data_dir>` is the active project's data dir — resolved from `--data-dir` / `litmus.yaml` `data_dir:` / `LITMUS_HOME` env / platform default; run `litmus daemon status` to see what's active. For the HTTP path, `litmus serve` must be running. For the Python path, only `litmus` itself.

## MCP Tool: `litmus_events`

```
# All events for a session
litmus_events(session_id="abc12345-...")

# Only measurement events
litmus_events(event_type="test.measurement")

# Instrument reads for the DMM
litmus_events(event_type="instrument.read", role="dmm")

# Events at or after a timestamp (`since` is inclusive; UTC, ISO 8601)
litmus_events(since="2026-03-10T14:00:00Z")

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

# Events at or after a time (`since` is inclusive; UTC)
curl "http://localhost:8000/api/events?since=2026-03-10T14:00:00Z&limit=50"
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

    # Events at or after a time (`since` is inclusive; pass UTC-aware datetimes —
    # stored timestamps are UTC, naive datetimes will compare incorrectly).
    from datetime import timezone
    recent = store.events(since=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc))

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
| `since` | ISO timestamp (UTC). Inclusive — `received_at >= since`. | `"2026-03-10T14:00:00Z"` |
| `limit` | Max results | `100` (default for HTTP / MCP; Python defaults to no cap) |

Role filtering checks `role`, `instrument_role`, and the `channel_id` prefix (anything before the first `.`) across all event types.

## See Also

- [Event Types Reference](../reference/event-types.md) — All event type fields
- [Event Log Architecture](../concepts/event-log.md) — How events are stored
- [MCP integration](mcp-integration.md) — Setting up `litmus_events` and the other MCP tools for AI clients
