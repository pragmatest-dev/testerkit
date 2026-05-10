# litmus.connect() Reference

`litmus.connect()` is the entry point for instrument access outside of pytest. Scripts, Jupyter notebooks, the operator UI, and background monitors use it to connect to instruments and log data through the event system.

**Source:** `docs/connect.md` (user guide), `litmus/data/event_log.py`, `litmus/data/event_store.py`

## Function Signature

```python
litmus.connect(
    station: str | None = None,
    *,
    data_dir: Path | str | None = None,
    mock: bool = False,
) -> StationConnection
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `station` | str \| None | `None` | Station ID. If `None`, reads `default_station` from `litmus.yaml` |
| `data_dir` | Path \| None | `None` | Where to write events. Falls back to `litmus.yaml` then `~/.local/share/litmus/data/` |
| `mock` | bool | `False` | Use mock instruments (skips resource locking) |

### Returns

`StationConnection` — context manager for instrument access.

### Station Config Resolution

1. `./stations/{station_id}.yaml` (project-local)
2. `~/.local/share/litmus/stations/{station_id}.yaml` (machine-global)

### Events Dir Resolution

1. Explicit `data_dir` parameter
2. `data_dir` from `litmus.yaml` in CWD ancestors
3. `~/.local/share/litmus/data/` (fallback)

## StationConnection

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `session_id` | UUID | This session's unique identifier |
| `event_log` | EventLog | The event log for this session |
| `config` | StationConfig | The station configuration |
| `instruments` | dict[str, Any] | Currently connected instruments by role |

### Methods

| Method | Description |
|--------|-------------|
| `start()` | Create EventLog, emit `SessionStarted` |
| `stop(outcome="complete")` | Release all instruments, emit `SessionEnded` |
| `instrument(role, timeout=0)` | Connect and lock an instrument by role |
| `release(role)` | Disconnect and unlock an instrument |

### Context Manager

```python
with litmus.connect("cell-7", mock=True) as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
# Automatic cleanup: instruments released, SessionEnded emitted
```

### Explicit Lifecycle

```python
station = litmus.connect("cell-7")
station.start()
dmm = station.instrument("dmm")
# ... work ...
station.release("dmm")
station.stop()
```

## Per-Resource Locking

Instruments are locked at the **resource address** level (e.g., `GPIB::16::INSTR`), not per station. Two scripts can use different instruments on the same station simultaneously.

```python
# Script A
station = litmus.connect("cell-7")
station.start()
dmm = station.instrument("dmm")      # locks GPIB::16::INSTR

# Script B (another terminal)
station = litmus.connect("cell-7")
station.start()
psu = station.instrument("psu")      # locks GPIB::17::INSTR — works!
dmm = station.instrument("dmm")      # raises ResourceInUse
```

Lock files use `fcntl.flock()` and live in `~/.local/share/litmus/locks/`. They auto-release when the process exits, even on SIGKILL.

### Cleanup Behavior

- Context manager (`with`) calls `stop()` automatically, which releases all instruments and emits `SessionEnded`
- If the process crashes, OS-level file locks are released automatically
- Mock instruments (`mock=True`) skip resource locking entirely

## Flight Server Lifecycle

When `StationConnection.start()` creates an `EventStore`, the store acquires a ref-counted DuckDB daemon via Flight:

1. First process spawns the daemon
2. Subsequent processes share it (ref-counted)
3. Daemon exits after idle timeout when all refs are released

This enables cross-process event queries — the operator UI can monitor events from a running pytest process.

## See Also

- [Sessions Concept](../concepts/sessions.md) — Why sessions exist
- [Managing Sessions Guide](../guides/managing-sessions.md) — Practical session workflows
