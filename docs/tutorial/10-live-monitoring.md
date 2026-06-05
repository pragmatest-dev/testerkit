# Step 10: Live Monitoring

**Goal:** Run tests while monitoring events and instrument data in real time.

## Prerequisites

- Completed [Step 7: Real Instruments](07-real-instruments.md) or using mock mode
- `litmus serve` running

## Start a Station Session

Open a Python script or Jupyter notebook:

```python
from litmus import connect

# Connect to your station (mock mode for this tutorial)
with connect("bench_1", mock=True) as station:
    dmm = station.instrument("dmm")

    # Every instrument interaction is logged as an event
    voltage = dmm.measure_voltage()

    print(f"Session ID: {station.session_id}")
    print(f"Voltage: {voltage}")
```

This creates a session, connects instruments, and logs all interactions to the [event store](../concepts/data/event-log.md) (see also [three-stores](../concepts/data/three-stores.md)).

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

The UI updates in real time as tests execute. Events flow through the system (see [concepts/event-log](../concepts/data/event-log.md) for `EventLog` / `EventStore` definitions):

```
pytest → EventLog.emit() → EventStore → UI subscription
```

## View the Run in Results

When pytest finishes, the run lands in the Results history. Open
[`http://localhost:8000/results`](http://localhost:8000/results) —
each pytest invocation that produced one or more tests appears as a
row. The list shows Outcome / Serial / Part Number / Hostname /
Project / Phase / Started / Steps / Meas / Ended. There's no
filter bar; columns are sortable and a stats strip above the
table summarizes the visible runs.

Click any row to drill into the detail view at `/results/<run_id>`.
The detail page is a sticky header card with a tab strip beneath:
**Overview** (run-level summary), **Steps** (one row per
`(step_path, vector_index)` execution with its outcome and
measurement count), **Measurements** (every value logged with its
limit and outcome), and **DUT History** (this DUT's prior runs).

For the full reference, see
[Operator UI → Results — list](../reference/operator-ui/results/list.md)
and [Operator UI → Results — detail](../reference/operator-ui/results/detail.md).

## See How the Line Is Doing

After a few runs accumulate, the
[`/metrics`](http://localhost:8000/metrics) page becomes the
go-to "is the bench healthy" view. A filter bar (Phase / Product /
Station / Lot / Since / Until) sits above a tab strip with six
analytical lenses:

| Tab | What it shows |
|---|---|
| Yield | First-pass yield, final yield, run / failure counts, a yield trend chart, and time stats |
| Pareto | Failure counts grouped by Product, Step, or Measurement (the group-by is a control on the tab) |
| Cpk | Per-measurement process capability, ranked worst-first |
| Retest | Time-bucketed retest rate — how many serials needed more than one attempt that period |
| Time loss | Wall-clock time spent on failed / errored runs per period |
| Assets | Per-instrument time share — Role / Resource / Sessions / Connected (s) / Share |

For the full reference, see
[Operator UI → Metrics](../reference/operator-ui/metrics.md).
For the diagnostic recipe behind the Retest signal, see
[Find flaky tests](../how-to/data/find-flaky-tests.md).

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

When instruments are read through the proxy, scalar values appear in events directly. Array data (waveforms) is stored in the [`ChannelStore`](../concepts/data/three-stores.md) (Litmus's time-series store for instrument arrays) with a `channel://` claim-check URI in the event:

```python
with connect("bench_1", mock=True) as station:
    scope = station.instrument("scope")
    waveform = scope.read_waveform()
    # Event contains: {"value": {"_ref": "channel://scope.ch1/...", "length": 1000}}
    # Actual waveform data is in the ChannelStore
```

Query channel data:

```bash
curl "http://localhost:8000/api/channels/scope.ch1?max_points=500"
```

See also: [Step 12: Continuous Monitoring](12-continuous-monitoring.md) — streaming into ChannelStore directly from an interactive script using `litmus.channels.stream`.

## What's Happening Under the Hood

1. `connect()` creates an `EventStore` and `EventLog` for the session
2. The EventStore acquires a [DuckDB Flight daemon](../concepts/data/flight-streaming.md) for cross-process queries
3. Each `emit()` writes to Arrow IPC files and pushes to DuckDB
4. The UI subscribes via `EventStore.on_event()` and receives events in real time
5. Channel data flows to `ChannelStore` with LTTB (Largest Triangle Three Buckets) decimation — a downsampling algorithm that preserves visual peaks — for display

← [Step 9: Production Ready](09-production.md)  |  [Tutorial index](index.md)

## Next Steps

- [Tour of the Operator UI](../how-to/overview/operator-ui-tour.md) — orientation map of all 14 sidebar entries
- [Find flaky tests](../how-to/data/find-flaky-tests.md) — diagnostic recipe combining Metrics + Results + parquet queries
- [Compare two runs](../how-to/data/compare-runs.md) — diff known-good vs failing
- [Event Log Architecture](../concepts/data/event-log.md) — How events work
- [Three Stores Architecture](../concepts/data/three-stores.md) — EventStore, ChannelStore, ParquetBackend
- [Querying Events](../how-to/data/querying-events.md) — All query patterns
- [Querying Channels](../how-to/data/querying-channels.md) — Channel query with LTTB
