# Choosing a Channel Verb — Write/Stream vs Latest/Live/Query

A channel is a named numeric time-series — a DMM reading, a chamber temperature, a scope trace. TesterKit splits the channel API into a **producer** side (you have data to record) and a **consumer** side (you want to read it). Picking the right verb is mostly about two questions: *how fast does the data arrive*, and *do you want to react to each value or just see the current one*.

## Producing — `write` vs `stream`

| You have… | Verb | Shape |
|---|---|---|
| One reading at a moment (a settling value, a step measurement) | `channels.write(name, sample)` | one-shot, returns a `channel://` URI |
| A continuous run of samples (a sweep, a continuous acquisition) | `with channels.stream(name) as sink:` | a sink you write each sample into; name the channel once |

```python
import testerkit.channels as channels

# one-shot
channels.write("chamber.temp", thermocouple.read())

# continuous
with channels.stream("scope.ch1") as sink:
    for _ in range(n):
        sink.write(scope.acquire())
```

A `sample` can be a scalar (a number) **or** an array (a whole waveform/buffer) — one row per acquisition either way.

## Consuming — pick by cadence and intent

Pick by what you're doing:

- Watching one number live (a gauge)? → `latest`
- Watching a fast signal / drawing a curve? → `live`
- Same, but the chart must look full right now? → `window`
- Reading back captured data (report, export, analysis, or a poll loop)? → `query`

Three of the consumer verbs keep calling you with new data as it arrives — use these to watch a value live. The fourth reads back data you already captured, once.

| You want… | Verb | Reads | Good for |
|---|---|---|---|
| The **current value**, updated when it changes | `channels.latest(name, cb)` | live, newest only | a gauge: chamber temp, supply readback, pressure |
| **Every sample** as it arrives, batched | `channels.live(name, cb, max_hz=…)` | live, every sample | a live chart of a fast signal: a trace, a sweep |
| The **last *N* seconds**, then keep going live | `channels.window(name, cb, dur=…)` | last N s, then live | a rolling chart that's already populated the moment it opens |
| A **range of past samples**, once | `channels.query(name, …)` | read back, once | analysis, export, a report, a periodic refresh |

```python
# gauge — newest value only, newest-wins (you never get a backlog of stale readings)
unsub = channels.latest("chamber.temp", lambda s: gauge.set(s.value))

# live chart — every sample, delivered as grouped batches, capped at 30/s
unsub = channels.live("scope.ch1", on_batch, max_hz=30)

# rolling window — the last 30s drawn immediately, then live (capped at 30/s)
unsub = channels.window("scope.ch1", on_batch, dur=30, max_hz=30)

# pull — the last 500 points for a report (poll this in a loop for a sparkline)
table = channels.query("chamber.temp", last_n=500)
```

### Why cadence decides it

The clearest way to choose: **slow → `latest`, fast → `live`.**

- A **chamber temperature** at ~0.5 Hz is a gauge. You want the current reading; the values in between don't matter. `latest` pushes the newest one and discards the rest — if your UI stalls, you get the *current* temperature, never a queue of old ones. Using `live` here would be odd: there's nothing to batch.
- A **scope channel** or an **IV sweep** is a trace. Every point draws the curve, so you want them all. `live` delivers them as grouped batches. `max_hz` caps how often you're called (a 1000-pixel chart can't show more than ~1000 points anyway), grouping samples in between.

### Watch live, or read in a loop

`latest` and `live` keep calling you — you hand TesterKit a function and it calls that function each time new data lands, until you call the returned `unsub()`. Use this when something (a UI, a chart) should update on its own.

To read on your own schedule, call `channels.query(...)` in a loop at whatever rate you want.

```python
# a refreshing "last 60" view — poll, no subscription held
while running:
    table = channels.query("chamber.temp", last_n=60)
    redraw(table)
    time.sleep(1)
```

### `window` — `live`, but already populated

`live` starts empty: a chart fed by `live` is blank until the next sample arrives. `window` solves the blank-chart problem — it hands you the last `dur` seconds first, then continues live with no gap and no repeated sample. Reach for it when a chart should look full the instant it opens (a "last 30 seconds" trace you can pop up mid-run), and for `live` when starting from empty is fine (a chart you open before the run begins).

```python
# opens already showing the last 30s, then keeps scrolling
unsub = channels.window("scope.ch1", chart.extend, dur=30, max_hz=30)
```

`dur` is the history depth in seconds; `max_hz` caps the live tail exactly as it does for `live`.

### Live is "from now"; the log is complete

The live feeds (`latest`/`live`) show data from the moment you start watching. If you need every point start-to-finish (an audit or lossless export), read it back with `channels.query(...)` — the stored record is always complete. (See [Data stores](../../concepts/data/data-stores.md) for how the ChannelStore and live feeds relate.)

## At a glance

```
produce:   write(one-shot)         stream(continuous)
consume:   latest(newest, live)    live(every sample, live)    query(range, read back)
           gauge / slow signal     chart / fast signal         analysis / report / poll
                                   window(last N s, then live)
                                   rolling chart, pre-filled
```

## See also

- [Stream a live channel](stream-live-channel.md) — the producer side, end to end
- [Querying channel data](querying-channels.md) — `query` filters (time range, `last_n`, `max_points` decimation)
- [Capture a waveform](capture-waveform.md) — array channels (a sample *is* a waveform)
