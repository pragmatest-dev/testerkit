# `observe` / `stream`

`observe` and `stream` are the two record-only test-author verbs — the
non-judging siblings of [`verify`](verify.md). Neither raises on the
value. Use them for setup readouts, characterization, captures, and
time-series logging.

## `observe` — record one observation

```python
observe(name: str, value: Any, *, namespace: str | None = None) -> None
```

Records `value` against `name` (stamps the `out_*` column on the active
vector) and routes by the value's shape — one verb for every kind of
evidence:

| Value | Routes to | Example |
|-------|-----------|---------|
| scalar (`float` / `int` / `str` / `bool`) | inline measurement | `observe("temp_c", 23.5)` |
| `Waveform` / numeric array | ChannelStore (time-series) | `observe("scope.cap", wf)` |
| blob (`bytes`, image, DataFrame, …) | FileStore → `file://` artifact | `observe("uut_photo", img)` |
| a `file://` / `channel://` URI | linked as-is | `observe("trace", uri)` |

The artifact or channel is linked from the run automatically, so it
appears in the operator UI's Measurements / Files / Channels views.
Available as a bare pytest fixture (`def test(observe, ...): ...`) and
as `context.observe(...)`.

## `stream` — append one sample to a channel

```python
stream(name: str, sample: Any, *, namespace: str | None = None) -> str
```

Appends a single `sample` to the `name` channel and returns its
`channel://` URI. Use it to log a value across a sweep or soak (a rail
under increasing load, a temperature ramp). Strictly orthogonal to
`observe` — it never stamps `out_*`; call `observe(name, sink)` if you
want the channel associated with the active vector.

For high-rate or multi-chunk capture, open a streaming sink instead of
calling `stream` per sample:

```python
import litmus

with litmus.channels.stream("dmm.voltage") as sink:
    for _ in range(1000):
        sink.write(dmm.measure_voltage())
```

`litmus.files.stream(name, format=...)` is the FileStore equivalent for
byte/record streams (`raw`, `jsonl`, `tdms`, `h5`).

## Which verb?

| Need | Verb |
|------|------|
| pass / fail against a limit | [`verify`](verify.md) |
| record a value / capture / artifact, no judgment | `observe` |
| append one time-series sample to a channel | `stream` |
| stream many samples / chunks at rate | `litmus.channels.stream` / `litmus.files.stream` sink |
