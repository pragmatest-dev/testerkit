# The Three Test-Author Verbs

Every value a test records plays one of three roles:

| Role | What it means | Written by |
|---|---|---|
| `input` | What you set — a commanded value, setpoint, or condition | `context.configure("vin", 5.0)` |
| `output` | What you read back — a response, environment reading, or captured artifact | `observe("v_rail", 3.3)` |
| `measurement` | The judged result with limits and a pass/fail outcome | `verify("v_rail", 3.31, limit=…)` / `measure(…)` |

The platform routes each call to the right store. You write the verb; the role follows automatically.

`stream` is a fourth verb — for continuous signals — and is covered below. It does not produce a role-keyed field on the vector; it writes to the channel timeline.

## What `configure` does

`context.configure` records the stimulus you commanded before the DUT responded:

```python
def test_load_regulation(psu, dmm, context):
    context.configure("psu.voltage", 5.0, unit="V")   # → input role
    context.configure("load.current", 1.0, unit="A")  # → input role

    reading = dmm.measure_dc_voltage()
    verify("vout", reading, limit=Limit(low=4.75, high=5.25, unit="V"))
```

The `input` values travel with the measurement row. When you query across runs, you can plot `vout` (measurement) against `psu.voltage` (input) to see load regulation without doing any joins.

## What `observe` does

`observe` records a value you read from the DUT or environment — the response side:

```python
observe("temp", temp_probe.read(), unit="°C")     # → output role
observe("v_rail", dmm.measure_dc_voltage())        # → output role, scalar
observe("scope_cap", scope.capture())              # → output role, Waveform
```

`observe` is polymorphic — the value's shape determines which store receives it:

| Value shape | Routes to | How to query later |
|---|---|---|
| Scalar (`int` / `float` / `bool` / `str`) | Inline on the measurement row | `FieldRef.output("v_rail")` |
| Array of scalars (list / ndarray) | ChannelStore | `/channels/{id}` chart panel |
| `Waveform` (Y + `sample_interval`) | ChannelStore | `/channels/{id}` chart panel |
| `XYData` (paired x/y arrays with per-axis units) | FileStore as `.npz` | `FieldRef.output("iv_curve")` → URI |
| Blob (image / `bytes` / `Path` / Pydantic model) | FileStore via serializer registry | `FieldRef.output("setup_photo")` → URI |

The verb you call decides that a value is an output. The value's shape decides where the bytes land. They're orthogonal.

## What `verify` and `measure` do

`verify` records a judged result — the measurement proper:

```python
verify("rise_time_us", rise_time(wf), limit=Limit(low=0, high=20, unit="us"))
# → measurement role: value, limits, outcome (PASSED / FAILED), unit
```

`measure` is the record-only sibling — it stamps `Outcome.DONE` and never raises on a missing limit:

```python
measure("rail_ripple", ripple(dmm.read_waveform()))
# → measurement role, no limit check
```

Both are scalar-only. Passing a `Waveform`, array, or blob to `verify` or `measure` raises `TypeError` with a message pointing at the two-verb pattern: `observe` the evidence first, then `verify` the derived scalar.

## Querying by role

Because role is stored with every field, the query API lets you reference any field by `(role, name)` — no fused prefixes, no column-name guessing:

```python
from litmus.queries import MeasurementsQuery, FieldRef

q = MeasurementsQuery()

# Plot vout (measurement) vs vin (input) across runs
rows = q.parametric(
    y=FieldRef.measurement("vout"),
    x=FieldRef.input("vin"),
)

# Plot temperature (output) vs date across runs
rows = q.parametric(
    y=FieldRef.output("temp"),
    x="run_started_at",     # bare string = fixed infrastructure column
)

# A bare string is measurement shorthand — the most common case
rows = q.parametric(y="vout", x=FieldRef.input("vin"))
```

`FieldRef.input("vin")` / `FieldRef.output("v_rail")` / `FieldRef.measurement("vout")` are the three constructors. A bare string resolves to `FieldRef.measurement(name)` because measurements carry limits and outcomes — they're what analysis is overwhelmingly about. Inputs and outputs always require the explicit `FieldRef`.

See [Reference → Query API](../../reference/data/query-api.md) for the full `MeasurementsQuery` surface.

## Discrete vs continuous — where `stream` fits

`observe` and `stream` both write to ChannelStore for array/waveform values. The difference is **intent**, not data shape:

| | `observe` (discrete) | `stream` (continuous) |
|---|---|---|
| Unit of data | One call, one captured value | The channel itself; samples are unnamed appends |
| Identity | Each call has a name and a vector context | The channel has a name; samples don't |
| Role on the vector | `output` (for scalars / URIs) | None — stream does not produce a role-keyed field |
| T&M shape | Triggered acquisition, scope capture, snapshot | Live sensor feed, free-run, continuous monitor |

**Rate doesn't decide. Intent does.** A 1-sample-per-5-minute temperature probe is continuous because the temperature is a signal that exists whether you sample it or not.

- **Continuous → stream** — samples accumulate on the channel timeline; the channel as a whole is the unit you query later
- **Discrete → observe** — each call is anchored to a specific moment in the test; queryable by `FieldRef.output(name)` for scalar outputs or URI navigation for waveforms

What breaks if you mix them up:

- `observe` in a high-rate loop → one `Observation` event per call (EventStore flood) + last-wins URI clobber on scalar outputs
- `stream` for a single discrete capture → no output stamped on the vector → can't navigate from the measurement to the supporting waveform without knowing the `channel_id` + time window

To associate a streamed channel with a measurement vector, pass the sink to `observe`:

```python
with litmus.channels.stream("scope.continuous") as cap:
    for _ in range(n_samples):
        cap.write(scope.read_trace())
    observe("scope.snapshot", cap)   # stamps cap.uri as an output on the active vector
```

## Two layers — test-author verbs vs store-direct

Litmus exposes data writing at two layers:

| Layer | Use when | Discrete | Continuous |
|---|---|---|---|
| **Test-author verbs** | Inside a test (has a vector context) | `observe(name, value)`, `verify(name, value, limit=…)` | `stream(name, sample)` |
| **Store-direct** | Outside a test — notebooks, scripts, operator UI | `channels.write(name, sample)` | `channels.stream(name)` |

The test-author verbs are built on top of the store-direct calls. What `observe` adds is vector bookkeeping: stamping the value's role and URI on the active vector, emitting an `Observation` event, handling URI latching. None of that makes sense outside a test, so the store-direct surface skips it.

`stream` does not associate with the vector automatically. Only `observe` stamps the vector. To associate a streamed channel with the active vector, pass the sink to `observe` as shown above.

## Engineering units

`configure`, `observe`, `verify`, `measure`, and `stream` all accept an optional `unit=` keyword. The unit is stored alongside the value and is visible in query results:

```python
context.configure("psu.voltage", 12.0, unit="V")
observe("temp", 24.8, unit="°C")
stream("current", sample, unit="A")
verify("output_voltage", dmm.measure_dc_voltage(), Limit(low=4.75, high=5.25, unit="V"))
```

For multi-axis data (IV curves, S-parameter sweeps, optical spectra), use `XYData` — it carries `x_unit` and `y_unit` as separate per-axis fields:

```python
from litmus.data.models import XYData

iv = XYData(x=[0.0, 0.5, 1.0, 1.5], y=[0.0, 2.1, 4.3, 6.8],
            x_unit="V", y_unit="mA", x_name="Bias", y_name="Current")
observe("iv_curve", iv)   # → FileStore .npz; output URI on the vector
```

## Capturing evidence and judging a metric together

The canonical pattern is `observe` for the raw evidence, then `verify` for the derived scalar:

```python
def test_psu_step_response(psu, scope, context):
    psu.set_voltage(5.0)
    wf = scope.capture()                               # block-mode acquisition

    observe("scope_cap", wf)                           # Waveform → ChannelStore
                                                        # output URI stamped on this vector

    verify("rise_time_us", rise_time(wf),
           limit=Limit(low=0, high=20, unit="us"))    # scalar → measurement row, judged
    verify("overshoot_v", overshoot(wf),
           limit=Limit(low=0, high=0.05, unit="V"))   # same vector, same scope_cap URI
```

Both `rise_time_us` and `overshoot_v` measurement rows carry the `scope_cap` channel URI. From any failing measurement on `/results/{run_id}` you can navigate directly to the supporting waveform.

## Streaming continuously while the test runs

For continuous capture across the test body, use the context-managed sink:

```python
import litmus.channels

with litmus.channels.stream("scope.continuous") as cap:
    for _ in range(n_samples):
        cap.write(scope.read_trace())
    observe("scope.snapshot", cap)   # stamps cap.uri as an output on the active vector
```

For byte-stream artifacts (video / audio / vendor capture), use `litmus.files.stream`:

```python
import litmus.files

with litmus.files.stream("camera", format="mp4") as cam:
    for frame in camera.read_frames():
        cam.write(frame)
```

`files.stream` requires `format=` — the platform cannot infer `mp4` vs `wav` vs `tdms` vs `raw` from opaque bytes. Every other dispatch (scalar vs array vs blob, ChannelStore vs FileStore, inline vs URI) is automatic.

## How streams are stored — segmented per session, unified by channel

A long-running stream is not one giant file. ChannelStore files are partitioned by date and by session:

```
data/channels/2026-06-03/env_temp_aaaa1111.arrow    ← Session A, day 1
data/channels/2026-06-04/env_temp_aaaa1111.arrow    ← Session A, day 2
data/channels/2026-06-03/env_temp_bbbb2222.arrow    ← Session B, day 1
```

The session boundary is the natural unit for retention, export, and crash isolation. The date partition keeps any single file from growing unbounded.

The logical channel identity is not partitioned. `channels.query("env.temp")` with no session filter spans every session that wrote to that channel. Add `session_id=…` to scope a query to one session.

## Concurrent sessions share instruments, not records

If two test sessions run concurrently and both share a scope (locked atomically by the platform), their captures are isolated on disk:

- ChannelStore: `session_id` is in the filename
- FileStore: `session_id` is in the directory
- EventStore: events from both sessions interleave on one timeline, but every event carries `session_id`
- ParquetBackend: one parquet file per run, never cross-run mixing

The instrument lock orders captures in time; storage isolation is independent. The one shared thing is the channel-id namespace — `/channels/scope_cap` shows both sessions' data unless filtered. That's deliberate: cross-session views matter for trends and fixture-channel accumulation.

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
- [Reference → Query API](../../reference/data/query-api.md) — `MeasurementsQuery`, `FieldRef`, and the role-based query surface
- [Reference → Parquet schema](../../reference/data/parquet-schema.md) — column conventions; `channel://` and `file://` URI formats
- [Reference → Channels](../../reference/operator-ui/channels/list.md) — the operator UI page where ChannelStore samples appear
