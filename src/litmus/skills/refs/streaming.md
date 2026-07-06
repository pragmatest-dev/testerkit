# `stream` — channel data

`stream` is the fourth sibling test-author verb (alongside `verify` / `measure`
/ `observe`). It appends one sample to a named **channel** — a time-series
store for continuous signals — and never touches the measurement or output
lane. Use it when a value is a *signal that exists whether you sample it or
not* (a soak temperature, a free-run scope acquisition, a live sensor feed),
not a one-off reading.

## Signature

```python
stream(name: str, sample: Any, *, namespace: str | None = None, unit: str | None = None) -> str
```

Appends `sample` to the `name` channel and returns its `channel://` URI.
`sample` is a scalar, array, `Waveform`, or dict (struct) — same shape rules
as `observe`'s array-handling path; blobs raise at the ChannelStore gate (use
FileStore for those). Available as a bare pytest fixture (`def
test(stream, ...): ...`) and as `context.stream(...)`.

**`stream` never stamps an output on the active vector** — it is an
append-to-stream operation, not "stash this on my current context." To wire
a streamed channel to the vector that captured it, pass the sink (or its
returned URI) to `observe`:

```python
observe("iv_curve.i", "channel://iv_curve.i")   # vector association
for v in [0.0, 0.5, 1.0, 1.5, 2.0]:
    psu.set_voltage(v)
    stream("iv_curve.i", dmm.read_current())
```

## `stream` vs `observe` / `measure` / `verify`

| Need | Verb |
|---|---|
| pass/fail against a limit | `verify` |
| a captured value or artifact, no judgment | `observe` |
| **samples accumulating over time on a named channel** | `stream` |

`observe` also routes arrays/`Waveform`s to ChannelStore — the difference is
intent, not data shape. `observe("scope_step", wf)` is one discrete capture,
anchored to this vector. `stream("dmm.voltage", v)` called in a loop is a
running channel; the channel itself, not any single call, is what you query
later. Full discrete-vs-continuous framing: `litmus refs show observe`.

## Power-user sink: `litmus.channels.stream`

For high-rate or multi-chunk capture, open a sink instead of calling the
`stream` fixture per sample — name the channel once, write many:

```python
import litmus.channels

with litmus.channels.stream("dmm.voltage") as sink:
    for _ in range(1000):
        sink.write(dmm.measure_voltage())
```

Same primitive whether called from inside a pytest test or a standalone
script/notebook with no test session at all (`examples/09-instrument-streaming`
drives a live operator UI panel this way — no pytest involved).

## Waveform evidence: capture, then judge

`examples/08-waveform-evidence` is the canonical `observe` + `verify` pairing
— capture the raw waveform backing a verified scalar, so a failing limit is
one click from the supporting trace:

```python
def test_psu_step_response(observe, verify, psu, scope) -> None:
    psu.set_voltage(5.0)
    wf = scope.capture()
    observe("scope_step", wf)  # routes to ChannelStore; stamps out_scope_step on this vector

    rise_us = compute_rise_time_us(wf, v_final=5.0)
    overshoot_v = compute_overshoot_v(wf, v_final=5.0)

    verify("rise_time_us", rise_us, Limit(low=0, high=20, unit="us"))
    verify("overshoot_v", overshoot_v, Limit(low=0, high=0.5, unit="V"))
```

The waveform routes to ChannelStore (not FileStore) because it's a typed
array plus a sample interval — exactly ChannelStore's shape. This is a
single `observe`, not `stream`: one capture, one vector.

## Continuous instrument streaming

`examples/09-instrument-streaming` is the other end — a standalone script
(no pytest) pushes one DMM reading every 20 ms for 60 seconds via
`channels.stream`, and the operator UI's `/channels/dmm.voltage` panel
renders it push-style as samples land. A real bench swaps the mock driver
for PyMeasure/PyVISA; the streaming call is unchanged. Consumers read the
same channel three ways — `channels.latest` (newest, for a gauge),
`channels.live` (every sample, for a chart), or the operator UI directly.

## Reading channel data back

ChannelStore files are session-scoped (`data/channels/{date}/{channel_id}_{session8}.arrow`)
but a channel query unifies every session that wrote to that name unless you
filter by `session_id`. Readback surfaces:

| Surface | How |
|---|---|
| Operator UI | `litmus serve` → `/channels/<channel_id>` |
| MCP tool | `litmus_channels` (`channel_id`, `session_id=`, `last_n=`, `max_points=`) |
| HTTP API | `GET /channels/{channel_id}`, `GET /channels` (list), `GET /channels/_recent` |
| Script / agent (one-shot) | `litmus.channels.query(name, last_n=..., max_points=...)` |
| Script / agent (live) | `litmus.channels.latest(name, cb)` / `.live(name, cb, max_hz=...)` |

There is no `litmus channels` CLI subcommand — channel readback is UI, MCP,
HTTP, or the `litmus.channels` module, not the CLI surface `litmus runs` /
`litmus show` use for run data.

## When NOT to stream

- **A scalar reading** (one temperature, one voltage) — that's `observe` or
  `verify`/`measure`, not `stream`. `litmus refs show observe` /
  `litmus refs show verify`.
- **An array or waveform captured once** — that's `observe`, which routes it
  to ChannelStore itself; no `stream` call needed. A plain blob (image,
  vendor binary, PDF) attached once is a FileStore artifact instead — see
  the blob row in `litmus refs show observe`.
- **A byte/record stream** (video, TDMS, raw vendor frames) — that's
  `litmus.files.stream(name, format=...)`, the FileStore equivalent; see
  `litmus refs show observe`.

## See also

`litmus refs show routing` — the front door: which verb, which lane, when to
reach for a channel at all.
