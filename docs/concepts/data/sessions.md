# Sessions as Observation Windows

A session is the window from connect to disconnect — the time a process is actively using instruments and logging data.

## What is a Session?

A session begins when a process calls `connect()` and ends when the connection is released. During a session, all events share the same `session_id`, making it easy to group and query related activity.

There's no sessions table. A session is simply every event that shares one `session_id` — Litmus groups them when you query. It "begins" and "ends" because the first and last events (`SessionStarted` / `SessionEnded`) mark the boundaries.

Sessions are broader than test runs. A single session might contain multiple test runs (e.g., retesting the same UUT), or no test runs at all (e.g., a calibration script or manual instrument exploration).

## Session Metadata

`SessionStarted` (see [event-log](event-log.md) for the full event list) records who ran it, on which station, and how (pytest, Jupyter, a script). Per-run context (UUT, part, test phase, git, environment) lives on `RunStarted`, emitted once per test run within the session.

| Category | Fields |
|----------|--------|
| **Session** | `session_type` |
| **Station** | `station_id`, `station_name`, `station_type`, `station_location`, `station_hostname` |
| **Process** | `pid`, `client` (pytest, jupyter, script name) |
| **Operator** | `operator_id`, `operator_name` |
| **Fixture / slot** | `fixture_id`, `slot_count` |

`RunStarted` (emitted once per test run within a session) carries the per-run context:

| Category | Fields |
|----------|--------|
| **UUT** | `uut_serial`, `uut_part_number`, `uut_revision`, `uut_lot_number` |
| **Part** | `part_id`, `part_name`, `part_revision` |
| **Slot** | `slot_id`, `slot_index` |
| **Test context** | `fixture_id`, `test_phase`, `project_name` |
| **Git** | `git_commit`, `git_branch`, `git_remote` |
| **Environment** | `environment_json` (Python version, litmus version, top-level deps, lockfile hash) |
| **Custom** | `custom_metadata` dict |

Config files (station, fixture, part spec) are tracked via git — the `git_commit` field on each `RunStarted` identifies the exact code and config state.

## Why Sessions Exist

Sessions solve three problems:

1. **Grouping events across runs** — Multiple test runs on the same UUT during one sitting share a session. You can query "everything that happened while bench-7 was connected" without knowing individual run IDs.

2. **Live monitoring** — The operator UI subscribes to events by `session_id` to show real-time progress. The session boundary tells the UI when to start and stop monitoring.

3. **Resource coordination** — Sessions track which instruments are in use, enabling per-resource locking. Two scripts can use different instruments on the same station simultaneously.

## The `connect()` API

```python
from litmus import connect

# Using a `with` block (scripts, notebooks)
with connect("cell-7", mock=True) as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
    # All interactions logged with this session's ID

# Explicit start/stop (UI, long-running processes)
station = connect("cell-7")
station.start()
dmm = station.instrument("dmm")
# ... work ...
station.stop()
```

`connect()` starts a session that:
- gets a new `session_id`
- emits `SessionStarted` with full context
- locks each instrument it uses, so two scripts can share a station
- emits `SessionEnded` when it closes

## See also
- [Event Log Architecture](event-log.md) — How events are stored and queried
- [Data stores](data-stores.md) — where the events behind a session are stored
- [connect() reference](../../reference/runtime/connect.md) — full API surface
- [Managing Sessions Guide](../../how-to/execution/managing-sessions.md) — Practical session management
