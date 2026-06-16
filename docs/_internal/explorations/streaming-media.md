# Follow-up: streaming media (MP4 / fMP4 / audio) over the live substrate

> **Status:** follow-up note, not yet scheduled. Captures why the streaming
> *substrate* is already the right shape for live media and what's actually
> missing. Surfaced while benchmarking file streaming (`files.stream_raw`) and
> discussing MP4 / streaming protocols. Internal doc.

## The substrate is already a streamed connection — and the right one

The live data plane is **held-open Flight (gRPC) streams behind a `PushRelay`**,
identical for channels and files:

- **Producer** — a persistent `do_put` stream, one handshake then push batches
  forever (`channels/store.py` `_flight_writers`; the files catalog_manager's
  held `do_put`).
- **Consumer** — a held-open `do_get` subscribe stream (`data/_flight_subscribe.py`
  — *"one do_get stream open, handing each batch to on_batch until unsub"*),
  push-style, explicitly *no poll* (`files/catalog_manager.py`).
- **Buffer** — `PushRelay`: bounded queue, **drop-oldest on overflow**, coalesce a
  drained burst into one batch, gap-detected; optional **conflate (`LATEST`)**.

Those semantics — *server-push, held open, lossy from-now tail, keep-latest,
gap-detected* — **are live-media semantics**. A live video feed wants exactly
"push frames, drop late ones, jump to latest, never block the camera." So this is
functionally an SSE / gRPC-server-streaming / WebSocket subscription, and it's the
same append + segment + offset model HLS / DASH / LL-HLS / fMP4 are built on (the
diary already cites LL-HLS for the byte-rejoin reason). **The transport is right.**

## What's actually missing

1. **Codec / muxer (the format).** Built-in stream formats are `raw`, `jsonl`,
   `tdms`, `h5`. **MP4/h264 (PyAV)** and **wav/flac (soundfile)** are already named
   follow-ups (`files/streaming.py` header — *build item 23, "hardware video
   encoder option pulls in PyAV"*). A media format is a `register_format` handler
   that muxes chunks into the container and feeds them to the existing `do_put`.

2. **Container rejoin (boundary-aware framing).** Two classes of stream, by how a
   live consumer reads a half-written file:
   - **Byte-appendable** (`raw`, `jsonl`) — rejoin at *any* byte offset; the
     `StreamCheckpoint(offset)` works directly.
   - **Container** (`tdms`, `h5`, **mp4**) — you cannot decode mid-bytes; a raw
     range read is garbage without the library. MP4 specifically needs
     **fragmented MP4 (fMP4)**: an init segment + self-contained media fragments,
     so a subscriber rejoins at a fragment boundary. The frame model carries this
     (a frame = a fragment) but the muxer must emit fragment-aligned chunks and the
     checkpoint offset must land on fragment boundaries.

3. **Browser-facing protocol bridge (the wire).** The subscribe wire is Flight /
   gRPC — a browser can't `<video src=…>` it. A live artifact/video viewer needs a
   **Flight-subscribe → HTTP fMP4 / HLS bridge** (init segment + media segments
   over HTTP, or MSE Source Buffer fed from the subscribe stream). This is the live
   artifact viewer the live-UI work points at (see `live-ui-pattern.md`).

## Benchmark caveat

`files.stream_raw` measures the **byte substrate ceiling** (64 KB chunks, no mux).
Container/media formats add per-chunk encode cost and fragment overhead, so their
throughput will be lower and bounded by the codec, not the disk. A media-format
scaling number needs its own workload once a muxer lands.

## Shape of the work (when scheduled)

- `register_format("mp4", …)` PyAV muxer emitting fragment-aligned chunks (+ wav/
  flac via soundfile) — build item 23.
- Make `StreamCheckpoint` fragment-boundary aware for container formats (offset =
  last complete fragment, not raw byte count).
- A `Flight-subscribe → fMP4/HLS` HTTP bridge for the operator-UI live viewer.
- A `files.stream_<fmt>` benchmark workload per media format (encode-bound, not
  disk-bound).
