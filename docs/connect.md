# litmus.connect() — Unified Instrument Access

`litmus.connect()` is the entry point for non-pytest instrument access. Any process — scripts, Jupyter notebooks, the NiceGUI operator panel, background monitors — can connect to instruments and log data through the event system.

## Quick Start

```python
import litmus

# Context manager (scripts)
with litmus.connect("cell-7", mock=True) as station:
    dmm = station.instrument("dmm")
    v = dmm.measure_voltage()
    # All interactions logged to event log

# Explicit lifecycle (UI)
station = litmus.connect("cell-7")
station.start()
dmm = station.instrument("dmm")
station.release("dmm")
station.stop()
```

## Per-Resource Locking

Instruments are locked at the **resource address** level (e.g. `GPIB::16::INSTR`), not per station. Two scripts can use different instruments on the same station simultaneously.

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

Lock files live in `~/.local/share/litmus/locks/` (Linux) and auto-release when the process dies, even on SIGKILL.

## Station Config Resolution

`litmus.connect("cell-7")` finds the station config in this order:

1. `./stations/cell-7.yaml` (project-local)
2. `~/.local/share/litmus/stations/cell-7.yaml` (machine-global)

## Events Dir Resolution

Event logs are written to:

1. Explicit `data_dir` parameter
2. `data_dir` from `litmus.yaml` in CWD ancestors
3. `~/.local/share/litmus/data/events/` (fallback)

## API Reference

### `litmus.connect(station, *, data_dir=None, mock=False)`

Returns a `StationConnection`. If `station` is `None`, reads `default_station` from `litmus.yaml`.

### `StationConnection`

| Method | Description |
|--------|-------------|
| `start()` | Create EventLog, emit SessionStarted |
| `stop(outcome="complete")` | Release all instruments, emit SessionEnded |
| `instrument(role, timeout=0)` | Connect and lock an instrument by role |
| `release(role)` | Disconnect and unlock an instrument |
| `instruments` | Dict of currently connected instruments |
| `session_id` | UUID of this session |
| `event_log` | The EventLog instance |
| `config` | The StationConfig |

Supports context manager (`with`) for automatic cleanup.

## Limitations

- File locks use `fcntl.flock()` — single-machine only. Cross-machine coordination is future work.
- Mock instruments with `mock=True` skip resource locking (no physical resource to coordinate).

## See Also

- [litmus.connect() Reference](reference/connect-api.md) — Full API reference with all parameters
- [Sessions Concept](concepts/sessions.md) — Why sessions exist and what they capture
- [Managing Sessions Guide](guides/managing-sessions.md) — Practical session workflows
