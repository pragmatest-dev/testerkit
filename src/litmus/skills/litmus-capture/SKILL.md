---
name: litmus-capture
description: Use when a user wants to capture or read back non-tabular test evidence with Litmus — a waveform, a live sensor feed, a photo, a vendor capture file, a log — anything that isn't a single scalar measurement.
---

# Capturing evidence (channels + files)

Picking the capture **verb** (`observe` vs `stream` vs a file write) is a
`litmus-tests` decision — see its verb table. This skill owns what happens
once you've picked a store: the write mechanics and how to read the data
back.

## 1. Which store?

| The value is… | Store | Call |
|---|---|---|
| a signal that exists whether you sample it or not (soak temp, live scope trace, sensor feed) | ChannelStore | `stream(name, sample, unit=...)` |
| an array/`Waveform` captured once, tied to this vector | ChannelStore (via `observe`) | `observe(name, waveform)` |
| a photo, vendor blob, structured report — one shot | FileStore | `observe(name, value)` or `litmus.files.write(...)` |
| a growing file (DAQ capture, jsonl log, vendor format) | FileStore | `litmus.files.stream(name, format=...)` |

A value's shape decides the store, not the call site — `observe` itself
routes scalars inline, arrays/`Waveform`s to ChannelStore, and everything
else (blobs, `BaseModel`s) to FileStore.

## 2. Channels — append a sample

```python
def test_soak_temp(stream, psu) -> None:
    psu.enable_output()
    for _ in range(60):
        stream("soak_temp", read_thermocouple(), unit="C")
```

`stream(name, sample, *, namespace=None, unit=None) -> str` (also
`context.stream(...)`) appends one sample and returns its `channel://`
URI. It never stamps an output on the active vector — `stream` and
`observe` are strictly orthogonal. To wire a channel to the vector that
captured it, `observe` the URI:

```python
observe("iv_curve.i", "channel://iv_curve.i")   # vector association
for v in [0.0, 0.5, 1.0, 1.5, 2.0]:
    psu.set_voltage(v)
    stream("iv_curve.i", dmm.read_current())
```

For high-rate or multi-chunk capture, open a sink once instead of calling
the fixture per sample — works inside a test or in a standalone script
with no pytest session at all:

```python
import litmus.channels

with litmus.channels.stream("dmm.voltage") as sink:
    for _ in range(1000):
        sink.write(dmm.measure_voltage())
```

## 3. Files — attach a blob or a growing artifact

One-shot, from inside a test — `observe` auto-routes:

```python
def test_uut_burn_in(observe, verify, psu) -> None:
    observe("uut_photo", snapshot_uut(serial="SN-DEMO-001"))   # PIL.Image -> .png
    observe("burn_report", BurnInReport(...))                  # BaseModel -> .json
```

One-shot, from outside a test (setup code, no active vector) — the
explicit form, same serializer dispatch, caller gets the URI back:

```python
uri = litmus.files.write("vendor_capture", vendor_blob)
```

A growing file — one artifact, many writes (`raw`, `jsonl`, `tdms`, `h5`):

```python
with litmus.files.stream("burn_log", format="jsonl") as log:
    observe("burn_log", log)   # links the sink's URI to the active vector
    log.write({"ts": ts, "event": "psu_on", "voltage_set": 5.0})
```

`litmus.files.write`/`.stream` require a `session_id` (auto-resolved from
the active session); `run_id` only lands in the sidecar when written
through these calls, not through `observe`'s auto-routing.

## 4. Reading it back

| Data | Surface |
|---|---|
| Channel, one-shot pull | `litmus.channels.query(name, last_n=..., max_points=...)` |
| Channel, live gauge/chart | `litmus.channels.latest(name, cb)` / `.live(name, cb, max_hz=...)` |
| Channel, agent/remote | MCP `litmus_channels(channel_id, session_id=, last_n=, max_points=)` |
| Channel, human | `litmus serve` → `/channels/<channel_id>` |
| Artifact catalog (metadata, not bytes) | MCP `litmus_files(uri=, session_id=, run_id=)` |
| Artifact, human | `litmus serve` → `/files` → `/files/{date}/{session_id}/{filename}` |
| Artifact, durable link to a run | query the vector's output value (`FieldRef.output(name)` — see `litmus-analysis`), then resolve the `file://` URI |

There is no `litmus channels` or `litmus files` CLI subcommand — channel
and artifact readback is UI, MCP, HTTP, or the `litmus.channels`/
`litmus.files` modules directly. `litmus show <run_id>` prints
measurements only; it doesn't list outputs or artifact URIs.

## Best-practice defaults

- **Shape decides the store** — don't reach for `litmus.files.write` on an
  array `observe` would already route to ChannelStore.
- **`stream` for anything you'd call a channel** (would still exist if
  nobody read it); a one-off capture is `observe`.
- **Filter artifact readback by `session_id`**, not `run_id`, to catch
  every artifact from a run — `observe`-routed blobs carry no `run_id`.

## Deeper
Read the docs:
```bash
litmus docs show how-to/data/capture-waveform
litmus docs show how-to/data/capture-an-artifact
litmus docs show how-to/data/stream-live-channel
litmus docs show how-to/data/querying-channels
litmus docs show concepts/data/three-verbs
```
Sibling skills: `litmus-tests` (verb choice), `litmus-analysis` (querying
the tabular data these artifacts attach to), `litmus-debug` (events around
a capture).
