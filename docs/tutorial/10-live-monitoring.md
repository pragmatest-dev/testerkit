# Step 10: Live Monitoring

**Goal:** Run tests while monitoring events and instrument data in real time.

## Prerequisites

- Completed [Step 7: Real Instruments](07-real-instruments.md) or using mock mode
- `litmus serve` running

## Start a Station Session

Open a Python script or Jupyter notebook:

```python
import litmus

# Connect to your station (mock mode for this tutorial)
with litmus.connect("bench_1", mock=True) as station:
    dmm = station.instrument("dmm")

    # Every instrument interaction is logged as an event
    voltage = dmm.measure_voltage()

    print(f"Session ID: {station.session_id}")
    print(f"Voltage: {voltage}")
```

This creates a session, connects instruments, and logs all interactions to the event store.

## Monitor in the UI

In another terminal:

```bash
litmus serve --reload
```

Open `http://localhost:8000` — the operator UI shows live session activity, including:

- Session metadata (station, DUT, operator)
- Instrument connections
- Measurements as they happen
- Step progress during test runs

## Run Tests While Monitoring

```bash
# In another terminal, run tests
pytest tests/ -s
```

The UI updates in real time as tests execute. Events flow through the system:

```
pytest → EventLog.emit() → EventStore → UI subscription
```

## Query Historical Data

After tests complete, query the results:

```bash
# Via HTTP API
curl http://localhost:8000/api/sessions
curl "http://localhost:8000/api/events?session_id=YOUR_SESSION_ID"
curl http://localhost:8000/api/channels
```

Or with the MCP tools:

```
litmus_sessions()
litmus_events(session_id="...")
litmus_channels(channel_id="dmm.voltage")
```

## Channel Data from Instrument Reads

When instruments are read through the proxy, scalar values appear in events directly. Array data (waveforms) is stored in the ChannelStore with a `channel://` URI in the event:

```python
with litmus.connect("bench_1", mock=True) as station:
    scope = station.instrument("scope")
    waveform = scope.read_waveform()
    # Event contains: {"value": {"_ref": "channel://scope.ch1/...", "length": 1000}}
    # Actual waveform data is in the ChannelStore
```

Query channel data:

```bash
curl "http://localhost:8000/api/channels/scope.ch1?max_points=500"
```

## What's Happening Under the Hood

1. `litmus.connect()` creates an `EventStore` and `EventLog` for the session
2. The EventStore acquires a DuckDB Flight daemon for cross-process queries
3. Each `emit()` writes to Arrow IPC files and pushes to DuckDB
4. The UI subscribes via `EventStore.on_event()` and receives events in real time
5. Channel data flows to `ChannelStore` with LTTB decimation for display

## Next Steps

- [Event Log Architecture](../concepts/event-log.md) — How events work
- [Three Stores Architecture](../concepts/three-stores.md) — EventStore, ChannelStore, ParquetBackend
- [Querying Events](../guides/querying-events.md) — All query patterns
- [Querying Channels](../guides/querying-channels.md) — Channel query with LTTB
