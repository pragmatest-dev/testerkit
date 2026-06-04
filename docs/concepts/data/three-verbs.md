# The Three Test-Author Verbs

In a test, you do one of three things with a measured value:

| Verb | T&M analogy | When to use |
|---|---|---|
| `verify(name, value, limit=…)` | A pass/fail measurement | One scalar, one judgment |
| `observe(name, value)` | A captured artifact (scope trace, screenshot, vendor blob) tied to a specific test moment | Evidence you want to remember alongside a measurement |
| `stream(name, sample)` / `channels.stream(name)` / `files.stream(name, format=…)` | A continuous signal — temp sensor, scope acquisition, DMM free-run, camera | Many samples that belong to a channel's timeline, not to one test moment |

The platform routes each call to the right store. You write the verb; the plumbing is handled.

## Discrete vs continuous — the core split

`observe` and `stream` both end up writing samples that ChannelStore (and on the consumer side, the operator UI) can read identically. The difference is **intent**, not data shape:

| | `observe` (discrete packet) | `stream` (continuous signal) |
|---|---|---|
| Unit of data | One call, one captured value | The channel itself; samples are unnamed appends |
| Identity | Each call has a name, vector context, event | The channel has a name; samples don't |
| Acknowledgment | Observation event per call | Lifecycle bookends only (`ChannelStarted` / `ChannelClosed`) |
| Routable from parquet | Yes — `out_<name>` URI on the measurement vector | No — must navigate via channel + time window |
| T&M shape | Triggered acquisition, scope capture, snapshot | Live sensor feed, free-run, continuous monitor |

**Rate doesn't decide. Intent does.** A 1-sample-per-5-minute temp probe is continuous because the temperature is a signal that exists whether you sample it or not, not because the rate is high. At any rate:

- **Continuous → stream** — samples accumulate on the channel timeline; the channel as a whole is the unit you query later
- **Discrete → observe** — each call is anchored to a specific moment in the test; vector navigation via `out_<name>` works

What breaks if you mix them up:

- `observe` in a high-rate loop → one `Observation` event per call (EventStore flood) + `out_<name>` clobbered on each call (last-wins; previous URIs lost from the vector)
- `stream` for a single discrete capture → no `out_<name>` stamp on the verify row → can't navigate from the measurement to the supporting waveform without knowing the channel_id + time window

## Two layers — test-author intent vs store-direct mechanics

Litmus exposes the data-write surface at two layers. Pick the layer that matches whether you have a vector to anchor to:

| Layer | Audience | Discrete | Continuous |
|---|---|---|---|
| **Test-author verbs** | Code inside a test (has vector/step/run context) | `observe(name, value)` | `stream(name, sample)` |
| **ChannelStore-direct** | Outside a test — interactive sessions, custom UIs, validation scripts, MCP tools | `channels.write(name, sample)` | `channels.stream(name)` |
| **FileStore-direct** | Same | `files.write(name, value)` | `files.stream(name, format=…)` |

The test-author verbs are built ON TOP OF the store-direct verbs. `observe` internally calls `channels.write` or `files.write` based on value shape. What `observe` adds is **test-context bookkeeping**: stamping `out_<name>` on the vector, emitting an `Observation` event, handling URI latching. None of that makes sense outside a test, so the store-direct surface skips it.

Practical guidance:

- **In a pytest test**: always use `verify` / `observe` / `stream`. They handle vector context for you.
- **In an interactive station session** (`litmus.connect("bench_1")` in a notebook): reach for `channels.write` / `channels.stream` / `files.write` / `files.stream` directly. There's no test vector to anchor to.
- **In a custom operator UI panel**: same — the panel is a consumer, not a producer of test-bound data.
- **In a long-running validation script** (not pytest-shaped): same — use the store-direct layer.

## Where the data lands

| Value shape | Routes to | Operator UI |
|---|---|---|
| Scalar (`int` / `float` / `bool` / `str`) | Inline → parquet `out_*` column on the measurement row | Results detail → Measurements tab (`/results/{run_id}`) |
| Array of scalars (list / ndarray) | ChannelStore array row | `/channels/{id}` chart panel |
| `Waveform` (Y + `sample_interval`) | ChannelStore array row + `sample_interval` | `/channels/{id}` chart panel |
| Blob (image / `bytes` / `Path` / Pydantic model) | FileStore via serializer registry | `/files`, `/files/{name}`; URIs also surface in parquet `out_*` columns on the run's measurements |

The verb you call decides whether you get vector linkage + per-call event. The value's shape decides which store the bytes land in. They're orthogonal.

`verify` is scalar-only. Passing a `Waveform`, array, or blob raises `TypeError` with a message pointing at `observe` and showing the two-verb pattern (`observe("scope_cap", wf)` first, then `verify("rise_time_us", derived, limit=…)`).

## How streams are stored — segmented per session, unified by channel

A long-running stream isn't one giant file. ChannelStore files are partitioned both by date and by session:

```
data/channels/2026-06-03/env_temp_aaaa1111.arrow    ← Session A, day 1
data/channels/2026-06-04/env_temp_aaaa1111.arrow    ← Session A, day 2
data/channels/2026-06-03/env_temp_bbbb2222.arrow    ← Session B, day 1
```

The session boundary is the natural unit for retention, export, and crash isolation — pruning Session A's segment doesn't touch Session B. The date partition keeps any single file from growing unbounded.

The **logical channel identity** is NOT partitioned. `channels.query("env.temp")` with no session filter spans every session that wrote to that channel; `/channels/env.temp` shows the unified timeline. Add `session_id=…` to scope a query to one session.

**Concrete scaling shapes:**

- 1 sample per 5 min × 1 year = ~105k rows ≈ < 10 MB. Trivial.
- 10 kHz scope sustained × hours = millions of rows. ARRAY_SCHEMA (one row per N-sample buffer) keeps this tractable; design-doc target is NVMe-bound.

## Capturing evidence and judging a metric together

The canonical pattern is `observe` for the raw evidence, then `verify` for the derived scalar:

```python
def test_psu_step_response(psu, scope, context):
    psu.set_voltage(5.0)
    wf = scope.capture()                              # block-mode acquisition

    observe("scope_cap", wf)                          # Waveform → ChannelStore
                                                       # out_scope_cap = channel://... on this vector

    verify("rise_time_us", rise_time(wf),
           limit=Limit(low=0, high=20, units="us"))   # scalar → parquet row, judged
    verify("overshoot_v", overshoot(wf),
           limit=Limit(low=0, high=0.05, units="V"))  # same vector, same out_scope_cap
```

The parquet rows for `rise_time_us` and `overshoot_v` both carry `out_scope_cap = channel://scope_cap?session=…`. From any failing measurement on `/results/{run_id}` you can navigate directly to the supporting waveform.

## Streaming continuously while the test runs

For continuous capture across the test body, use the context-managed sink. The sink satisfies the `Latchable` protocol, so you can hand it to `observe` to associate a snapshot with the active vector:

```python
import litmus.channels

with litmus.channels.stream("scope.continuous") as cap:
    for _ in range(n_samples):
        cap.write(scope.read_trace())
    observe("scope.snapshot", cap)   # stamps cap.uri on out_scope.snapshot
```

For byte-stream artifacts (video / audio / vendor capture), use `litmus.files.stream`:

```python
import litmus.files

with litmus.files.stream("camera", format="mp4") as cam:
    for frame in camera.read_frames():
        cam.write(frame)
```

`files.stream` requires `format=` — the platform can't infer `mp4` vs `wav` vs `tdms` vs `raw` from opaque bytes. That's the format library's call, not the platform's. Every other dispatch (scalar vs array vs blob, ChannelStore vs FileStore, inline vs URI) is automatic.

## Concurrent sessions share instruments, not records

If two test sessions run concurrently and both share a scope (locked atomically by the platform), their captures are isolated on disk:

- ChannelStore: session_id is in the filename, so two sessions writing `observe("scope_cap", wf)` create separate files
- FileStore: session_id is in the directory, so two sessions writing `observe("setup.png", img)` create separate paths with distinct URIs
- EventStore: events from both sessions interleave in one shared timeline, but every event carries `session_id` so queries that filter by session see only one session's events
- ParquetBackend: one parquet file per run, never cross-run mixing on disk

The instrument lock orders captures in time; storage isolation kicks in independently. **Shared instruments, isolated records.** ✓

The one shared thing is the channel_id NAMESPACE — `/channels/scope_cap` shows both sessions' data unless filtered. That's deliberate (cross-session views matter for trends and fixture-channel accumulation) and per-run UIs pass the session filter automatically.

## Custom types

Unknown value types fall back to pickle with a `RuntimeWarning` that names the type and points at `register_serializer`. Register once and your type is auto-routed thereafter:

```python
from litmus.data.files import register_serializer

register_serializer(
    MyInstrumentFrame,
    extension=".bin",
    mime="application/octet-stream",
    write=lambda value, dest: dest.write_bytes(value.to_bytes()),
)
```

Alternatively, implement `litmus_serialize(dest: Path) -> Path` on your type — no registration call needed.

## See also

- [Three Stores Architecture](three-stores.md) — where parquet, ChannelStore, and FileStore live on disk and how they relate
- [Tutorial 11 — Waveforms and evidence](../../tutorial/11-waveforms-and-evidence.md) — pytest test with `observe` + `verify`
- [Tutorial 12 — Continuous monitoring](../../tutorial/12-continuous-monitoring.md) — interactive session with `channels.stream` + live UI
- [Reference → Parquet schema](../../reference/data/parquet-schema.md) — `out_*` column conventions; `channel://` and `file://` URI formats
- [Reference → Channels](../../reference/operator-ui/channels/list.md) — the operator UI page where ChannelStore samples appear
