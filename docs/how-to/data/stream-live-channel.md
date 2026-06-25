# Stream Continuous Instrument Data into a Live Channel

Stream continuous instrument readings from a Python script or REPL into a named Litmus channel, and watch the operator UI panel update push-style as samples land.

> **Prerequisites.** A station YAML at `stations/{station_id}.yaml` with at least one instrument role declared; a concrete driver class with a single-sample read method (or a self-simulating one like the example below); `litmus serve` running in a separate terminal for the live UI.

## Step 1: Connect to the station

```python
from litmus import connect

with connect("bench_01") as station:
    ...
```

`connect` reads `bench_01` from `stations/bench_01.yaml` (or the `default_station` in `litmus.yaml` when no argument is passed). The `with` block opens and closes the session for you; Ctrl-C mid-stream closes it cleanly and leaves the samples already written on disk. See [sessions](../../concepts/data/sessions.md) for how session scope works.

Outside pytest, this is the entry point. The pytest plugin handles the equivalent session bookkeeping for you during a test run; here you own it directly.

## Step 2: Open a channel to stream into

```python
import litmus.channels

with litmus.channels.stream("dmm.voltage") as ch:
    ...
```

`litmus.channels.stream(...)` opens a named channel inside a `with` block. The channel name (`dmm.voltage`) is what you'll see in the operator UI and query with `channels.query`. Open it inside the `connect` block so the session is active when samples land.

Inside a pytest test, use the `stream(name, sample)` fixture instead — it writes one sample per call and is wired to the active run. The `litmus.channels.stream(...)` shown here is the same channel, opened directly from a script or REPL outside a test.

## Step 3: Push samples in a loop

```python
import time
import litmus.channels
from litmus import connect

RATE_HZ = 50.0
DURATION_S = 60.0

def main() -> None:
    interval_s = 1.0 / RATE_HZ

    with connect("bench_01") as station:
        dmm = station.instrument("dmm")

        n = int(RATE_HZ * DURATION_S)
        with litmus.channels.stream("dmm.voltage") as ch:
            for i in range(n):
                ch.write(dmm.measure_voltage())
                if i % 50 == 0:
                    print(f"  {i + 1}/{n} samples")
                time.sleep(interval_s)
```

`station.instrument("dmm")` returns the driver instance declared under role `dmm` in the station YAML. Each `ch.write(value)` appends one sample to ChannelStore. `time.sleep` controls the rate; for higher rates, remove it and let the instrument call pace the loop.

A minimal `stations/bench_01.yaml` for this example:

```yaml
id: bench_01
name: Bench 01
station_type: bench
instruments:
  dmm:
    type: dmm
    driver: drivers.DMM
    resource: TCPIP::192.168.1.102::INSTR
```

## Step 4: Watch live in the operator UI

Open `http://localhost:8000/channels/dmm.voltage`. The channel panel appears once the first sample lands and updates as each sample arrives — no page reload. For how live updates are delivered, see [Flight streaming](../../concepts/data/flight-streaming.md).

Run the script a second time and both sessions' samples appear on the same `dmm.voltage` timeline. To scope a query to one session, pass `session_id=` to [channel queries](querying-channels.md).

## Step 5: Consume it from your own code (not just the UI)

The operator UI is one consumer; your own script or agent can watch the channel with the same verbs the UI uses. Run this while the producer above is streaming:

```python
import litmus.channels as channels

# the newest value, conflated — a gauge (fires when the value changes)
stop_gauge = channels.latest("dmm.voltage", lambda s: print("now:", s.value))

# every sample, delivered as coalesced batches — a live chart edge
stop_chart = channels.live("dmm.voltage", lambda b: print("+", b.num_rows, "samples"))

# the last 30s drawn immediately, then live — a chart that opens already full
stop_window = channels.window("dmm.voltage", on_batch, dur=30)

# ... later
stop_gauge()
stop_chart()
stop_window()
```

`latest`, `live`, and `window` each call your function every time new data arrives, until you call the stop handle they return. `window` first replays the last `dur` seconds, then continues live. For a one-shot read (or to poll a refreshing view) use `channels.query(...)` instead. See [Choosing a channel verb](choosing-a-channel-verb.md) for which to reach for; a runnable consumer lives at `examples/09-instrument-streaming/scripts/live_dmm_reader.py`.

## See also

- [Choosing a channel verb](choosing-a-channel-verb.md) — write/stream vs latest/live/query, by cadence and intent
- [The Three Test-Author Verbs](../../concepts/data/three-verbs.md) — when to use `stream` vs `observe`
- [Tutorial 12 — Continuous monitoring](../../tutorial/12-continuous-monitoring.md) — full walkthrough of this pattern from scratch, including the self-simulating DMM driver and on-disk layout
- [Query channel data](querying-channels.md) — time-range queries, session filtering, MCP and HTTP API
- [Capture a waveform and judge derived scalars](capture-waveform.md) — discrete single-capture pattern inside a pytest test
