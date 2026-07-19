# Stream Continuous Instrument Data into a Live Channel

Stream continuous instrument readings from a Python script or REPL into a named TesterKit channel, and watch the operator UI panel update push-style as samples land.

> **Prerequisites.** A station YAML at `stations/{station_id}.yaml` with at least one instrument role declared; a concrete driver class with a single-sample read method (or a self-simulating one like the example below); `testerkit serve` running in a separate terminal for the live UI.

## Step 1: Connect to the station

```python
from testerkit import connect

with connect("bench_01") as station:
    ...
```

`connect` reads `bench_01` from `stations/bench_01.yaml` (or the `default_station` in `testerkit.yaml` when no argument is passed). The `with` block opens and closes the session for you; Ctrl-C mid-stream closes it cleanly and leaves the samples already written on disk. See [sessions](../../concepts/data/sessions.md) for how session scope works.

Outside pytest, this is the entry point. The pytest plugin handles the equivalent session bookkeeping for you during a test run; here you own it directly.

## Step 2: Open a channel to stream into

```python
import testerkit.channels

with testerkit.channels.stream("dmm.voltage") as ch:
    ...
```

`testerkit.channels.stream(...)` opens a named channel inside a `with` block. The channel name (`dmm.voltage`) is what you'll see in the operator UI and query with `channels.query`. Open it inside the `connect` block so the session is active when samples land.

Inside a pytest test, use the `stream(name, sample)` fixture instead — it writes one sample per call and is wired to the active run. The `testerkit.channels.stream(...)` shown here is the same channel, opened directly from a script or REPL outside a test.

## Step 3: Push samples in a loop

```python
import time
import testerkit.channels
from testerkit import connect

RATE_HZ = 50.0
DURATION_S = 60.0

def main() -> None:
    interval_s = 1.0 / RATE_HZ

    with connect("bench_01") as station:
        dmm = station.instrument("dmm")

        n = int(RATE_HZ * DURATION_S)
        with testerkit.channels.stream("dmm.voltage") as ch:
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
import testerkit.channels as channels

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

## Writing without a sink: one-shot and batch

The `stream` sink is the right tool for a live producer loop — it buffers samples and flushes them as columnar blocks (up to a size cap or a flush interval, set in `testerkit.yaml` under `channels:`), trading a little per-sample latency for throughput. When you're not running a sample-by-sample loop, two explicit module-level verbs cover the rest:

```python
import testerkit.channels as channels

# one sample, one message — the explicit form of the stream verb
channels.write("dmm.voltage", dmm.measure_voltage())

# a batch you already hold, sent in one message — the N samples ride a
# single message instead of N, so a buffer you've already collected lands
# far faster than N separate write() calls
readings = [(v, None) for v in dmm.read_block()]   # (value, sampled_at) pairs
channels.write_many("dmm.voltage", readings)
```

`write_many` takes `(value, sampled_at)` pairs — `value` is any shape `write` accepts (scalar, array, dict) and `sampled_at` is each sample's hardware instant, or `None`. Reach for it when you already have the samples in hand: an instrument that returns a block of readings, or a logged buffer you're replaying. It doesn't add latency of its own — it amortizes the per-message cost across the whole batch. (The `stream` sink uses the same batched core internally; the latency-for-throughput trade there comes from its buffering, not from the batch write itself.) Both `write` and `write_many` run inside an active session, the same as `stream`.

To pin a channel's unit (or other identity) before the first sample, declare it once — otherwise the first write auto-registers the channel with defaults:

```python
channels.declare("dmm.voltage", unit="V")
```

## See also

- [Choosing a channel verb](choosing-a-channel-verb.md) — write/stream vs latest/live/query, by cadence and intent
- [The Three Test-Author Verbs](../../concepts/data/three-verbs.md) — when to use `stream` vs `observe`
- [Tutorial 12 — Continuous monitoring](../../tutorial/12-continuous-monitoring.md) — full walkthrough of this pattern from scratch, including the self-simulating DMM driver and on-disk layout
- [Query channel data](querying-channels.md) — time-range queries, session filtering, MCP and HTTP API
- [Capture a waveform and judge derived scalars](capture-waveform.md) — discrete single-capture pattern inside a pytest test
