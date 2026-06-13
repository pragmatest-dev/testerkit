# Choosing a Channel Verb — Write/Stream vs Latest/Live/Query

A channel is a named numeric time-series — a DMM reading, a chamber temperature, a scope trace. Litmus splits the channel API into a **producer** side (you have data to record) and a **consumer** side (you want to read it). Picking the right verb is mostly about two questions: *how fast does the data arrive*, and *do you want to react to each value or just see the current one*.

## Producing — `write` vs `stream`

| You have… | Verb | Shape |
|---|---|---|
| One reading at a moment (a settling value, a step measurement) | `channels.write(name, sample)` | one-shot, returns a `channel://` URI |
| A continuous run of samples (a sweep, a continuous acquisition) | `with channels.stream(name) as sink:` | a sink you push into; name the channel once |

```python
import litmus.channels as channels

# one-shot
channels.write("chamber.temp", thermocouple.read())

# continuous
with channels.stream("scope.ch1") as sink:
    for _ in range(n):
        sink.write(scope.acquire())
```

A `sample` can be a scalar (a number) **or** an array (a whole waveform/buffer) — one row per acquisition either way.

## Consuming — pick by cadence and intent

There are four consumer verbs. Three are **subscriptions** (push — the platform calls your function as data lands) and one is a **query** (pull — you ask for it).

| You want… | Verb | Style | Good for |
|---|---|---|---|
| The **current value**, updated when it changes | `channels.latest(name, cb)` | push, conflated | a gauge: chamber temp, supply readback, pressure |
| **Every sample** as it arrives, batched | `channels.live(name, cb, max_hz=…)` | push, lossless-while-keeping-up | a live chart of a fast signal: a trace, a sweep |
| The **last *N* seconds**, then keep going live | `channels.window(name, cb, dur=…)` | push (history first, then live) | a rolling chart that's already populated the moment it opens |
| A **range of past samples**, once | `channels.query(name, …)` | pull (one-shot) | analysis, export, a report, a periodic refresh |

```python
# gauge — newest value only, conflated (you never get a backlog)
unsub = channels.latest("chamber.temp", lambda s: gauge.set(s.value))

# live chart — every sample, delivered as coalesced batches, capped at 30/s
unsub = channels.live("scope.ch1", on_batch, max_hz=30)

# rolling window — the last 30s drawn immediately, then live (capped at 30/s)
unsub = channels.window("scope.ch1", on_batch, dur=30, max_hz=30)

# pull — the last 500 points for a report (poll this in a loop for a sparkline)
table = channels.query("chamber.temp", last_n=500)
```

### Why cadence decides it

The clearest way to choose: **slow → `latest`, fast → `live`.**

- A **chamber temperature** at ~0.5 Hz is a gauge. You want the current reading; the values in between don't matter. `latest` pushes the newest one and conflates — if your UI stalls, you get the *current* temperature, never a queue of old ones. Using `live` here would be odd: there's nothing to batch.
- A **scope channel** or an **IV sweep** is a trace. Every point draws the curve, so you want them all. `live` delivers them as coalesced batches — a consumer that falls behind catches up in one read instead of one callback per sample. `max_hz` caps how often you're called (a 1000-pixel chart can't show more than ~1000 points anyway), coalescing in between.

### Subscription vs poll

A subscription (`latest`/`live`) is a **callback** — you register a function and the platform calls it, on a background thread, until you call the returned `unsub()`. That fits anything event-driven (a UI keeps running and updates when data arrives).

If instead you want a **loop** — "read, do something, repeat" — that's just polling: call `channels.query(...)` at your own rate. There's no separate "iterate a subscription" verb, because a loop *is* a poll. A refreshing sparkline is `query(last_n=N)` called every frame.

```python
# a refreshing "last 60" view — poll, no subscription held
while running:
    table = channels.query("chamber.temp", last_n=60)
    redraw(table)
    time.sleep(1)
```

### `window` — `live`, but already populated

`live` starts empty: a chart fed by `live` is blank until the next sample arrives. `window` fixes the cold start — it hands you the last `dur` seconds of history *first*, then continues live from the same point with no gap and no repeated sample. Reach for it when a chart should look full the instant it opens (a "last 30 seconds" trace you can pop up mid-run), and for `live` when starting from empty is fine (a chart you open before the run begins).

```python
# opens already showing the last 30s, then keeps scrolling
unsub = channels.window("scope.ch1", chart.extend, dur=30, max_hz=30)
```

`dur` is the history depth in seconds; `max_hz` caps the live tail exactly as it does for `live`.

### Live is "from now"; the log is complete

The live feed (`latest`/`live`) is **from-now** — it delivers what arrives after you subscribe, and under extreme overload it drops the oldest live values rather than stalling the producer or disconnecting you. The durable record is always complete: if you need *every* point with no gaps (an audit, a lossless export), read it back with `channels.query(...)` — the log is the source of truth, the live feed is a low-latency view of the leading edge. (`window`'s history prefill comes from that same complete log.)

## At a glance

```
produce:   write(one-shot)         stream(continuous)
consume:   latest(newest, push)    live(every sample, push)    query(range, pull)
           gauge / slow signal     chart / fast signal         analysis / report / poll
                                   window(last N s, then live)
                                   rolling chart, pre-filled
```

## See also

- [Stream a live channel](stream-live-channel.md) — the producer side, end to end
- [Querying channel data](querying-channels.md) — `query` filters (time range, `last_n`, `max_points` decimation)
- [Capture a waveform](capture-waveform.md) — array channels (a sample *is* a waveform)
