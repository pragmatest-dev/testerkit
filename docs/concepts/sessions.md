# Sessions as Observation Windows

A session represents a connect-to-disconnect lifecycle — the window during which a process is actively using instruments and logging data.

## What is a Session?

A session begins when a process calls `litmus.connect()` and ends when the connection is released. During a session, all events share the same `session_id`, making it easy to group and query related activity.

Sessions are broader than test runs. A single session might contain multiple test runs (e.g., retesting the same DUT), or no test runs at all (e.g., a calibration script or manual instrument exploration).

## Session Metadata

`SessionStarted` captures the full context at session start:

| Category | Fields |
|----------|--------|
| **Station** | `station_id`, `station_name`, `station_type`, `station_location`, `station_hostname`, `slot_id` |
| **Process** | `pid`, `client` (pytest, jupyter, script name) |
| **DUT** | `dut_serial`, `dut_part_number`, `dut_revision`, `dut_lot_number` |
| **Product** | `product_id`, `product_name`, `product_revision` |
| **Operator** | `operator_id`, `operator_name` |
| **Test Context** | `fixture_id`, `sequence_id`, `test_phase`, `git_commit` |
| **Environment** | `environment_json` (Python version, litmus version, top-level deps, lockfile hash) |
| **Custom** | `custom_metadata` dict, `channel_refs` list |

Config files (station, fixture, product spec) are tracked via git — the `git_commit` field on each run identifies the exact code and config state.

## Why Sessions Exist

Sessions solve three problems:

1. **Grouping events across runs** — Multiple test runs on the same DUT during one sitting share a session. You can query "everything that happened while bench-7 was connected" without knowing individual run IDs.

2. **Live monitoring** — The operator UI subscribes to events by `session_id` to show real-time progress. The session boundary tells the UI when to start and stop monitoring.

3. **Resource coordination** — Sessions track which instruments are in use, enabling per-resource locking. Two scripts can use different instruments on the same station simultaneously.

## The `litmus.connect()` API

```python
import litmus

# Context manager (scripts, notebooks)
with litmus.connect("cell-7", mock=True) as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
    # All interactions logged with this session's ID

# Explicit lifecycle (UI, long-running processes)
station = litmus.connect("cell-7")
station.start()
dmm = station.instrument("dmm")
# ... work ...
station.stop()
```

`litmus.connect()` creates a `StationConnection` that:
- Generates a new `session_id`
- Creates an `EventLog` for this session
- Emits `SessionStarted` with full context
- Manages per-resource instrument locking
- Emits `SessionEnded` on close

## See Also

- [Event Log Architecture](event-log.md) — How events are stored and queried
- [Three Stores Architecture](three-stores.md) — Where session data lives
- [litmus.connect() Reference](../reference/connect-api.md) — Full API reference
- [Managing Sessions Guide](../guides/managing-sessions.md) — Practical session management
