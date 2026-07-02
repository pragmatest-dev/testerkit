# `connect()` reference

`connect()` is the entry point for non-pytest instrument access. Scripts, Jupyter notebooks, the operator UI, and background monitors all use it to acquire a `StationConnection` that owns the event log, the channel store, and the locked instruments for the session.

## Function signature

```python
from litmus import connect

connect(
    station: str | None = None,
    *,
    data_dir: Path | None = None,
    mock: bool = False,
) -> StationConnection
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `station` | `str \| None` | `None` | Station id. `None` reads `default_station` from `litmus.yaml` in the CWD ancestors. |
| `data_dir` | `Path \| None` | `None` | Where to write events / channels. Resolution: explicit arg → `litmus.yaml` `data_dir:` → `LITMUS_HOME` → the platform default user-data directory. |
| `mock` | `bool` | `False` | Use mock instruments. Skips resource locking — multiple mock connections can hold the same role. |

Returns a `StationConnection`. Usable as a context manager (`with connect(...) as station:`) or with the explicit `start()` / `stop()` calls.

## Quick start

```python
from litmus import connect

# Context-manager form — typical for scripts
with connect("cell-7", mock=True) as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
    # All driver calls + measurements stream into the event log

# Explicit lifecycle — typical for the operator UI
station = connect("cell-7")
station.start()
try:
    dmm = station.instrument("dmm")
    # ... work ...
    station.disconnect("dmm")
finally:
    station.stop()
```

The context-manager exit picks the session outcome from the exception type: `None` → `passed`, `KeyboardInterrupt` / `SystemExit` → `terminated`, anything else → `errored`.

## `StationConnection`

Constructor: `StationConnection(station_config: StationConfig, *, data_dir: Path | None = None, mock: bool = False)` — typically constructed for you by `connect()`.

### Properties

| Property | Type | Description |
|---|---|---|
| `session_id` | `UUID` | Unique id of this session. |
| `config` | `StationConfig` | The loaded station configuration. |
| `instruments` | `dict[str, Any]` | Currently connected instruments by role. |
| `event_log` | `EventLog \| None` | Active event log (after `start()`). |
| `event_store` | `EventStore \| None` | Active event store (after `start()`). |
| `channel_store` | `ChannelStore \| None` | Active channel store (after `start()`). |
| `instrument_server_address` | `str \| None` | `host:port` of the instrument server, if running. |

### Start / stop

| Method | Description |
|---|---|
| `start()` | Create `EventLog`, emit `SessionStarted`, open `ChannelStore`, connect and lock the session's instruments, register process-exit cleanup. |
| `stop(outcome: str = "passed")` | Release all instruments, emit `SessionEnded`, close the event / channel stores. |

### Instrument access

| Method | Returns | Description |
|---|---|---|
| `instrument(role, timeout: float = 0)` | proxied driver | Connect and lock a single instrument by role. Raises `ResourceInUse` if the underlying resource address is locked. |
| `disconnect(role)` | `None` | Disconnect and unlock a single instrument. |
| `configure(role, method, **parameters)` | `None` | Emit an `InstrumentConfigure` event — for UI-initiated operations the user needs in the event log. |
| `start_instrument_server(roles: set[str] \| None = None)` | `str` (`host:port`) | Start the instrument server so external processes can share these instruments. |

### Events + observations

| Method | Returns | Description |
|---|---|---|
| `events(*, event_type=None, role=None)` | `list[dict]` | Read events from this session's log. Both filters are optional. |
| `on_event(callback, *, event_type=None, role=None, since=None)` | `Callable[[], None]` (unsubscribe) | Subscribe to events. Replays matching past events first, then pushes new ones as they arrive. |
| `observe(key, value, *, unit=None, sample_interval=None)` | `str` (`channel://` URI) | Append a sample to the `ChannelStore`. Returns the `channel://` URI other events can reference. |
| `sync(name, timeout: float \| None = None)` | `None` | Wait at a named sync point. Used for multi-UUT site coordination. |

### Context-manager protocol

`__enter__` calls `start()`; `__exit__` calls `stop(outcome=...)` with the outcome derived from `exc_type` (see above). Re-entrant `with` blocks are not supported — one `StationConnection` per lifetime.

## Per-resource locking

Locks are at the **resource address** level (e.g. `GPIB::16::INSTR`), not per-station. Two scripts can hold different instruments on the same station simultaneously:

```python
# Script A
station_a = connect("cell-7")
station_a.start()
dmm = station_a.instrument("dmm")      # locks GPIB::16::INSTR

# Script B (separate process)
station_b = connect("cell-7")
station_b.start()
psu = station_b.instrument("psu")      # locks GPIB::17::INSTR — works
dmm = station_b.instrument("dmm")      # raises ResourceInUse
```

Lock files live in `~/.local/share/litmus/locks/` (Linux) and use an OS file lock. They auto-release when the process exits, even on `SIGKILL` — single-machine only; cross-machine coordination is future work.

`mock=True` connections skip locking entirely; multiple mock sessions can hold the same role.

## Flight server lifecycle

When `start()` creates an `EventStore`, the store acquires a ref-counted DuckDB daemon via [Flight](../../concepts/data/flight-streaming.md):

1. First process to start a session spawns the daemon.
2. Subsequent processes share it (ref-counted).
3. The daemon exits after an idle timeout once all refs are released.

This is what lets the operator UI tail events from a running pytest process — both processes connect to the same daemon.

## Station-config resolution

`connect("cell-7")` finds the station YAML in this order:

1. `./stations/cell-7.yaml` (project-local)
2. `~/.local/share/litmus/stations/cell-7.yaml` (machine-global)

If `station` is `None`, it reads `default_station` from `litmus.yaml` in the CWD ancestors.

## See also

- [Sessions](../../concepts/data/sessions.md) — why sessions exist and what they capture
- [Managing sessions](../../how-to/execution/managing-sessions.md) — practical workflows
- [Flight streaming](../../concepts/data/flight-streaming.md) — the DuckDB daemon `connect()` rides on
- [Litmus fixtures](../pytest/fixtures.md) — the pytest equivalents (every fixture is backed by the same `StationConnection` machinery)
