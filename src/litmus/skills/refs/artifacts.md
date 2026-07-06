# Artifacts (FileStore)

Picking the right verb: `litmus refs show routing`. Streaming channels
(numeric time-series) vs artifacts (blobs): `litmus refs show observe`.

FileStore holds anything that isn't a scalar or a channel-shaped numeric
series — images, vendor capture files, structured reports, line-delimited
logs. Companion to ChannelStore; a value's shape decides which store it
lands in, not the call site.

## Attaching one artifact — `observe` (test-author path)

```python
def test_uut_burn_in(observe, verify, psu) -> None:
    observe("uut_photo", snapshot_uut(serial="SN-DEMO-001"))   # PIL.Image → .png
    observe("vendor_capture", vendor_blob)                     # bytes → .bin
    observe("burn_report", BurnInReport(...))                  # BaseModel → .json
```

`observe(name, value)` stamps `value` as an `output` on the active vector
and, when the value's shape doesn't fit inline (not a scalar) or a
channel (not a `Waveform`/array), routes it through FileStore's
serializer dispatch and links the resulting `file://` URI as that
output's value. See `litmus refs show observe` for the full shape-routing
table.

## Attaching one artifact — power-user path

```python
import litmus.files

uri = litmus.files.write("vendor_capture", vendor_blob)   # -> "file://2026-07-03/<session_id>/vendor_capture.bin"
```

`litmus.files.write(name, value, *, namespace=None, session_id=None,
vector_id=None, attributes=None)` is the explicit form — same
serializer dispatch as `observe`, but it does not stamp an output on
the active vector; the caller gets the URI back and links it however
it wants (e.g. from setup code with no active test). **Every call
creates a new immutable artifact** — a repeated `name` collides on
`_2`, `_3`, ... suffixes rather than overwriting. `write`, not `put`:
the underlying `FileStore.write` is append-a-new-record, never an
idempotent replace.

## Byte streams — one artifact, written incrementally

```python
with litmus.files.stream("burn_log", format="jsonl") as log:
    observe("burn_log", log)          # latches the sink's URI onto the vector
    log.write({"ts": ..., "event": "psu_on", "voltage_set": 5.0})
    for cycle in range(5):
        log.write({"ts": ..., "event": "sample", "cycle": cycle, "voltage": v})
```

`litmus.files.stream(name, *, format, namespace=None, session_id=None,
vector_id=None, attributes=None)` opens a `StreamingSink` for **one
growing artifact** — every `.write(chunk)` appends to that same file
(`raw`, `jsonl`, `tdms`, `h5` formats). This is the byte-stream sibling
of `litmus.channels.stream` (one typed numeric sample per call); see
`litmus refs show observe` for the channel side. The sink emits a
`file://` URI on close; `observe(name, sink)` inside the `with` block
links that URI to the active vector as an output, same as any other
`observe` call.

## How an artifact links to a run

`session_id` is always required — it's the on-disk partition
(`file://{date}/{session_id}/{filename}`). `run_id` is optional and
only lands in the sidecar when written through `litmus.files.write` /
`litmus.files.stream` (auto-resolved from the active run); an artifact
`observe()` auto-routes from a blob value carries `session_id` only —
its sidecar's `run_id` is `null`. `vector_id`, when passed, prefixes
the on-disk filename for an audit trail — it is never persisted in the
sidecar. There is no `file_id` foreign key on the run's parquet: the
durable link *to* the run is the `file://` URI value `observe` stamped
on that vector's output. Query it back with `FieldRef.output("uut_photo")`,
then resolve the URI against FileStore.

## Reading an artifact back

- **MCP tool / HTTP API**: `litmus_files` — lists catalog rows
  (`uri`, `name`, `session_id`, `run_id`, `created_at`, ...) filtered by
  `uri`, `session_id`, or `run_id`. Filtering by `run_id` only surfaces
  artifacts whose sidecar carries one (see above) — filter by
  `session_id` to catch every artifact from a run's session. Returns
  catalog metadata, not bytes — fetch the artifact itself by its
  `file://` URI.
- **CLI**: no dedicated `litmus files` subcommand and `litmus show <run_id>`
  prints measurements only — it does not list outputs or artifact URIs.
  Query outputs via the Query API (`FieldRef.output(name)`) or the
  `litmus_files` MCP tool above.
- **Operator UI**: `/files` lists every artifact; click through to
  `/files/{date}/{session_id}/{filename}` for the detail view (inline
  render for images/JSON/text, download for opaque binaries).

## Artifacts vs streaming channels vs observe

| Need | Reach for |
|---|---|
| judge a scalar against a limit | `litmus refs show verify` |
| record a scalar/waveform/blob, no judgment | `litmus refs show observe` |
| one typed numeric sample per call, time-series | `litmus.channels.stream` — `litmus refs show observe` |
| a growing file (log, DAQ capture, vendor format) | `litmus.files.stream` (this page) |
| one-shot file (photo, report, vendor capture) | `observe(name, value)` or `litmus.files.write` (this page) |
