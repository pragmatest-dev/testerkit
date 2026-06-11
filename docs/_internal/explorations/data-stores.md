# Data architecture: stores, verbs, and the unifying timeline

**Status:** consolidated design note. Merges `data-stores-claim-check.md` (v1 baseline) and `data-stores-refinements.md` (v1.1 verb/dispatch/SDK refinements) into one cohesive design. The two source notes are kept in `_internal/explorations/` as iteration history; this is the canonical document going forward.

**Audience:** contributors. Internal — file:line citations and internal class names fine here. Do not link from public docs.

**Reading order:** top-to-bottom is the intended learning path — assignment → stores → verbs → routing → manifestation → events → power-user surfaces → consumer SDK → lifecycle → performance → build items → prior art. Each section assumes only what came before.

---

## 1. The assignment: what T&M test data is for

Test & measurement data is **not telemetry or logs — it is evidence that bears responsibility**, and it has to serve four jobs at once, for a long time. Every store / claim / index / verb decision below traces back to one of these:

1. **Disposition (the gate).** Did *this* unit meet spec? Pass/fail, bin, ship-or-scrap — per-unit, against limits derived from the part spec.
2. **Traceability & provenance (the defense).** A durable, reconstructable record tying a **serial** → the **spec/limits** judged against → the **instruments + calibration** that measured it → **station, operator, conditions, revision, timestamp**. For RMAs, recalls, audits, regulatory. Must stay trustworthy and survive years — you may have to *prove* a unit passed long after the bench is gone.
3. **Yield & process control (the population).** Across units/lots/stations/time: yield, Pareto, Cpk, drift, retest. Needs the data **normalized and queryable** in aggregate.
4. **Diagnosis & characterization (the signal).** When something fails — or pre-production, mapping the device across its envelope — the **raw waveform/image/trace** is the evidence. Heavy, reached for occasionally, but must be **findable**.

Cross-cutting, all four demand the data be **trustworthy** (can't fake or silently lose — why dirty-git demotes, why calibration gates, why events are immutable), **complete** (even unrun steps recorded), and **findable** (worthless at scale otherwise).

**Traceability is the spine.** Every datum carries, or is joinable to, the context that makes it *evidence* rather than a bare number. A capture with no traceable context is diagnostically and legally inert. This is why parquet denormalizes serial/spec/station/cal/operator/conditions onto each row — and why "where does a waveform's session/run context live" is a load-bearing question, not a detail.

### How the four jobs map to the architecture

| Job | Served by |
|---|---|
| Disposition | `verify` / limits / outcome → measurement rows in parquet |
| Traceability / evidence | the **immutable event timeline** (source of truth) |
| Yield / SPC | the **normalized parquet** (analysis view) |
| Diagnosis | **channels + files** (raw signal & artifacts) |
| Findability across all | **claim URIs + per-store indexes** (attribute indexes long-term) |
| Lean + durable evidence | **claim-check** (heavy data lives in tech-appropriate stores; events reference) |

---

## 2. Stores at a glance — different homes for different shapes

The system uses **four stores**, each shaped for a specific data type. They aren't parallel competitors — they serve genuinely different access patterns. **The event log unifies them**: every meaningful operation emits an event, and the event carries either the data inline (when small) or a **claim URI** pointing at the right store (when heavy).

| Store | On disk | Holds | Claim URI | Optimized for | Lifetime |
|---|---|---|---|---|---|
| **EventStore** | `events/{date}/{session_id}.arrow` | immutable timeline of typed events; small metadata + claims | — | append-only ordering, ordered replay, traceability spine | indefinite |
| **ChannelStore** | `channels/{date}/{channel_id}_{session_short}.arrow` (segment-rotated) | typed numeric time-series (scalars or arrays per row) | `channel://id?session=…` | live per-sample subscribe + per-sample query + high-rate writes | days to weeks (retention-windowed) |
| **FileStore** | `_ref/{session}/{name}.{ext}` (unified) | discrete artifacts: images, video, vendor blobs (.tdms), JSON, anything with file semantics | `file://_ref/…` | durable archive, render-on-fetch, format-portable | indefinite |
| **ParquetBackend** | `runs/{date}/{ts}_{serial}.parquet` | denormalized analysis rows (one per measurement) | — | per-row analytic queries (yield, Cpk, Pareto, SPC) | indefinite |

The first three are **source-of-truth stores**. ParquetBackend is the **materialized analytic view** built from the event log at `RunEnded`.

### Why four stores instead of one

Each is optimized for an access pattern the others can't serve well:

| If we collapsed everything into … | What we'd lose |
|---|---|
| Just EventStore (everything is an event) | per-sample queries (events have opaque JSON payload); subscription granularity (every consumer gets every event); high write throughput for numerics |
| Just ChannelStore (everything is a typed time-series) | heterogeneous events don't fit the row schema; lifecycle / state events lose their typed envelope |
| Just FileStore (everything is a file) | typed per-sample subscribe (files yield bytes-at-offset, not typed rows); per-sample windowed query (must parse the format to filter); raw-bytes consumers need format-aware decoders |
| Just ParquetBackend (everything is a parquet row) | live updates (parquet is materialized at RunEnded); raw bytes (parquet rows aren't for binary blobs); event timeline ordering |

The split earns its weight: each store serves what it's specifically good at; the event log unifies them via claim URIs so consumers walk one timeline and dereference claims to reach the actual bytes.

### Write performance per store (order-of-magnitude estimates)

These are **expected ranges**, not benchmarked promises. End-to-end Flight throughput isn't measured today — `test_data/test_perf.py` covers local writes only; build item 19 below adds the end-to-end bench.

| Store | Write path | Realistic throughput | What limits it |
|---|---|---|---|
| EventStore | Arrow IPC + JSON payload + Flight `do_put` per event | ~10–50 MB/s sustained; ~1–10K events/s | per-event JSON encode + envelope; per-row notify to live subscribers |
| ChannelStore (SCALAR_SCHEMA) | Arrow IPC, one row per sample, per-sample notify | ~1–5 MB/s per channel sustained; ~100K–500K samples/s aggregate | per-sample notify cost dominates; multiple channels in parallel scale aggregate |
| ChannelStore (ARRAY_SCHEMA) | Arrow IPC, one row per buffer (N samples) | **~100–500 MB/s** for 1–10 kHz buffer-rate captures | amortized notify per buffer; ideal for high-rate continuous numerics |
| FileStore put (one-shot) | Direct file write via serializer | bound by serializer: Pillow PNG ~50–100 MB/s; `np.save` NPZ ~500 MB/s+; raw bytes → disk speed | format library + disk (NVMe: 1–7 GB/s; HDD: ~100 MB/s) |
| FileStore stream (sink) | Direct file write via format library (PyAV / soundfile / etc.) | video H.264 via PyAV ~50–200 MB/s; raw byte streams → disk speed | encoder CPU + disk |
| ParquetBackend (materialization) | Batch write at RunEnded | not throughput-critical — sized to one run | one-shot per run; no continuous load |

Three write-side takeaways:
- For **high-rate continuous numerics**, ARRAY_SCHEMA on ChannelStore is the right tool (~100s of MB/s). Per-sample SCALAR_SCHEMA is fine for moderate rates (DMM polling, sensor sampling) but per-sample notify limits it.
- For **video and large continuous captures**, FileStore's streaming sink wrapping a format library (PyAV) hits encoder-bound throughput, not store-bound throughput. The store is just the orchestrator.
- **EventStore write volume drops dramatically under Position 2** (channel events are lifecycle-only; see §8). Pre-Position-2 estimates assumed ~1 event per channel sample; under Position 2 it's ~1 event per channel per session (`ChannelStarted`) + ~1 per close. For a 10 kHz channel that wrote 10M samples in a session, the EventStore sees **2 events**, not 10 million. The ~1-10K events/s number above stays accurate for *significant* events (lifecycle, measurements, errors); the per-sample flood that would push much higher just doesn't happen.

### Read performance per store

Read paths have their own bottlenecks, separate from writes. Where the architecture bites hardest today is **EventStore payload filtering** — the opaque JSON column means any predicate on payload fields (channel_id, min/max, units, limits) is full-scan + per-row JSON parse.

| Store | Read pattern | Realistic throughput today | What limits it |
|---|---|---|---|
| EventStore | envelope-only filter (event_type, session_id, received_at, event_number) | ~100–500 MB/s | typed columns; DuckDB columnar pushdown; fast |
| EventStore | **payload-field filter** (any JSON content) | **~5–50 MB/s** | **JSON parse per row** — no statistics, no pruning, no pushdown |
| EventStore | live subscribe (Flight `do_get`) | ~10–100 MB/s on loopback | wire serialization + per-event notify |
| ChannelStore | per-sample query (windowed) | ~50–200 MB/s SCALAR; ~100–500 MB/s ARRAY | typed read is already fast; DuckDB pushdown |
| ChannelStore | live subscribe (Flight `do_get`) | ~50–200 MB/s on loopback | wire serialization; multiple channels add aggregate fan-out |
| FileStore | full file read | disk-bound (~500 MB/s – 7 GB/s NVMe) | disk + protocol overhead (~5–10% for HTTP) |
| FileStore | range read (partial) | disk-bound | same as above |
| FileStore | **video decode** (consumer side) | ~50–200 MB/s | **software H.264 decode CPU-bound** |
| Parquet | analytical scan | ~500 MB/s – 5 GB/s | already well-optimized (column projection + predicate pushdown native) |

Three read-side takeaways:
- **EventStore payload filtering is the slowest read in the system today** (~5–50 MB/s) and is the biggest single read-side bottleneck. Build item 21 (typed Arrow payloads) lifts this by 10–50× because payload fields become columnar with DuckDB pushdown.
- **ChannelStore and Parquet reads are already well-optimized** — Arrow columnar + DuckDB. Remaining gains come from transport (build item 22 — local shared-memory).
- **FileStore video decode** is software CPU-bound today on the consumer side. Mirror of the build item 23 encoder story: a hardware-decode option (build item 24) gains 10–20× for video playback in UIs.

### Read performance after the typed-payload + shared-memory refactors

| Store / path | Read today | After items 21/22 (24 for video decode) |
|---|---|---|
| EventStore envelope filter | 100–500 MB/s | 200–1000 MB/s (cleaner schema) |
| EventStore **payload filter** | 5–50 MB/s (JSON parse) | **100–500 MB/s** (typed pushdown — 10–50× win) |
| EventStore live subscribe | 10–100 MB/s | **GB/s** (local shared-memory) |
| ChannelStore at-rest query | 100–500 MB/s | 200 MB/s – 3 GB/s (mmap + compression) |
| ChannelStore live subscribe | 50–200 MB/s | **GB/s** (local shared-memory) |
| FileStore full / range read | disk-bound | unchanged |
| FileStore video decode | 50–200 MB/s (sw) | **500 MB/s – 2 GB/s** (hw decode — L7b) |
| Parquet analytical scan | 500 MB/s – 5 GB/s | unchanged |

**Build item 21 wins more on reads than writes.** Item 21 was sold above as a 5–10× write improvement; the read side is **10–50× on payload filters** — the more important number for analytic workflows. It's also what closes the "events aren't a search surface" gap: once payloads are columnar, **EventStore itself becomes searchable** without needing a separate per-store index layer.

**Build item 22 is symmetric** on reads and writes — both ends benefit from shared-memory zero-copy. The qualitative win for consumers is consumer CPU dropping to near-zero (no more wire deserialization per Flight batch); meaningful for live UIs that today have to deserialize every batch.

### Performance headroom — improvement levers

The current estimates assume v0.2.0 architecture as-designed. There's substantial headroom from incremental optimizations and one significant local-only architectural move. "As fast as possible locally" is an explicit goal — these are the levers.

#### Per-store bottlenecks → fixes

**EventStore (~10–50 MB/s)** — biggest cost is the opaque JSON payload column (`event_log.py:31-40`). Every event JSON-encodes its full payload; consumers JSON-parse on read.

| Fix | Gain | Lift |
|---|---|---|
| Replace JSON payload with typed Arrow structs per event type — events.py already has typed Pydantic models; lay them into native nested Arrow columns | **5–10×** write + read | medium — schema migration, per-event-type record batches; eliminates JSON entirely; also closes the "events aren't a search surface" gap for envelope-style queries |
| Batch event emission — buffer N events before flush | 2–5× for high-rate event streams | small — tune `BufferedIPCWriter` threshold |
| Split high-volume event types into their own streams — `InstrumentRead` is the dominant volume; separate file from rare lifecycle events | 2–3× for `InstrumentRead`-heavy workloads | small — segment-rotation strategy |

**ChannelStore SCALAR (~1–5 MB/s/channel)** — per-sample notify is the cost (`store.py:324,327`).

| Fix | Gain | Lift |
|---|---|---|
| Batch samples into Arrow record batches per channel before flush | 5–10× for high-rate scalars | medium — touches writer path; subscriber-latency vs throughput tradeoff |
| Byte-aware flush threshold (build item 19) | stable behavior under load, not raw throughput | small |
| Server-side per-subscriber filtering — only notify subscribers that asked for this channel | 3–5× when many subscribers + many channels | medium |
| `ARRAY_SCHEMA` opt-in for streams that can tolerate buffer-rate subscribe | 10–50× | none — usage pattern, just document |

**ChannelStore ARRAY (~100–500 MB/s)** — already close to disk-bound; incremental headroom.

| Fix | Gain | Lift |
|---|---|---|
| Arrow column compression (LZ4 / Zstd) on `samples` | 1.5–3× effective throughput at CPU cost | small — Arrow IPC supports natively |
| Memory-mapped local writes (mmap-append) | 1.5–2× for local-only | medium — alternative writer path |
| Coalesce rapid array-write notifies (debounce when writes land in <1 ms) | reduces subscriber pressure | small |

**FileStore (variable; format-library bound)** — store itself is thin; throughput dominated by the format library.

| Fix | Gain | Lift |
|---|---|---|
| Hardware video encoders (NVENC / VAAPI / VideoToolbox) | **10–20×** vs software H.264 (50 MB/s → 500 MB/s – 2 GB/s) | small — PyAV exposes hardware encoders; expose as `hwaccel="auto"` on the streaming sink |
| Raw-bytes fast path — bypass serializer registry for `bytes` / `bytearray` puts | 2–3× for large-blob puts | trivial |
| Memory-mapped Path-copy for large existing files (`os.copy_file_range` on Linux) | 2–5× for big copies | small |
| Parquet vs `np.save` for `ndarray` — compressed parquet often faster | 1.5–2× for big arrays | trivial — registry choice |

#### Local-first wins (the biggest single architectural opportunity)

Flight-over-loopback is sized for network; local consumers can do significantly better.

| Move | What it buys | Lift |
|---|---|---|
| **Shared-memory IPC for local subscribers** — Arrow IPC buffers exposed via `multiprocessing.shared_memory` or POSIX `shm`; local UIs read zero-copy | **3–10× for local subscribers**; consumer CPU near zero | medium — transport-selection branch in Consumer SDK (Flight for network; shm for local) |
| **Memory-mapped reads at rest** for `.arrow` files — DuckDB + PyArrow support natively | 2–3× for at-rest queries; near-disk-speed | trivial — don't force load-into-memory |
| **Single-process embedded mode** — test runner + UI + materializer co-resident in one process, share heap; no IPC at all | 5–10× because no serialization | medium — "co-resident" path that bypasses Flight when not crossing process boundaries |

For a typical local deployment (operator UI + pytest on the same machine), shared-memory is the dominant unlock. Estimated end-to-end live throughput lands in the **GB/s range** for channel data + sub-millisecond UI update latency.

#### Different-stack moves (real but bigger)

| Move | Gain | Trade-off |
|---|---|---|
| Rust / C++ Flight server backend (vs pyarrow's Python server) | 2–5× transport; better tail latency | meaningful rewrite; loses Python ease |
| `io_uring` (Linux) / IOCP (Windows) for async batched I/O | 2–4× for sustained streams | OS-specific code paths |
| GPU-direct buffer ingress (DAQ → CUDA → ChannelStore without CPU round-trip) | 5–10× for specific GPU-DAQ workflows | niche; vendor SDK cooperation |
| DuckDB native ingestion — write directly to DuckDB-managed parquet | 2× materialization; query speed unchanged | loses live-replay characteristic of Arrow IPC |

For Litmus's positioning (Python-native, pytest-integrated, accessible), the Rust/C++ rewrite isn't worth chasing for v0.2.0. The shared-memory + native-Arrow-payload moves get most of the headroom without leaving Python.

#### Expected combined picture if obvious levers are pulled

| Store / path | Today | After incremental wins | After local-shm move |
|---|---|---|---|
| EventStore | 10–50 MB/s | 50–200 MB/s (typed payload) | 100–500 MB/s (typed + shm) |
| ChannelStore SCALAR | 1–5 MB/s/channel | 5–20 MB/s/channel (batching) | 20–100 MB/s/channel (+ shm) |
| ChannelStore ARRAY | 100–500 MB/s | 200–800 MB/s (compression + mmap) | **near disk-bound** (1–3 GB/s on NVMe) |
| FileStore raw bytes | disk-bound | unchanged (already disk-bound) | unchanged |
| FileStore video (sw) | 50–200 MB/s | unchanged | unchanged |
| FileStore video (hwaccel) | not available | **500 MB/s – 2 GB/s** | unchanged |

"As fast as possible locally" lands roughly at:
- **Channel data:** 1–3 GB/s sustained (NVMe-bound) with ARRAY_SCHEMA + compression + shm
- **Events:** 100–500 MB/s with typed payloads + batched flush
- **Video:** 500 MB/s – 2 GB/s with hardware encoders
- **One-shot artifacts:** disk speed (NVMe: 1–7 GB/s)

That's 10–20× headroom over current conservative estimates, achievable mostly through known incremental moves — none of which require leaving the Python + Arrow + DuckDB stack. The build items (19, 21, 22, 23 in §12) capture the work.

---

## 3. The user surface — three verbs

The whole consumer-facing API for test authors is **three verbs**. They're polymorphic on value type for the routing-as-dispatch cases (`observe` / `verify`); explicit per-store for the operational streaming case (`stream`).

| Verb | Audience | Concern | Touches |
|---|---|---|---|
| `observe(name, X)` | test author | "associate X with my vector at this moment" | dispatches by value type — inline / ChannelStore / FileStore |
| `verify(name, X, limit=None)` | test author | observe + emit a measurement row (judged if limit; DONE if not) | same dispatch as observe |
| `stream(name, sample)` | test author | "push one sample into a continuous record" | always ChannelStore; **never** auto-associates |

Plus the driver-facing and power-user surfaces:

| Verb / API | Audience | Concern |
|---|---|---|
| `observer.read(channel, value, method)` | driver author (inside instrument code) | recording call: writes channel + emits `InstrumentRead` (naming smell — see Build items) |
| `channels.write(name, sample)` | power user | one-shot append to a channel |
| `with channels.stream(name) as ch:` | power user | context-managed channel writer |
| `filestore.put(name, value)` | power user | one-shot artifact (serializer dispatches format) |
| `with filestore.stream(name, format=…) as sink:` | power user | context-managed byte sink (video, large captures) |

### `observe` and `verify` accept any of four input shapes

| Value | Destination | What `out_<name>` carries |
|---|---|---|
| Scalar (`float`/`int`/`bool`/`str`) | inline in the event payload | the scalar |
| Channel-shaped numeric (`Waveform`, `numpy.ndarray` of numerics) | ChannelStore (ARRAY_SCHEMA row per call) | `channel://…?session=…` |
| File-shaped blob (`Path`, `bytes`, `PIL.Image`, video frame, vendor blob, `BaseModel`, `DataFrame`) | FileStore (one artifact per call) | `file://…` |
| Channel handle / `channel://` URI / file URI / Path-already-in-FileStore | (no re-write) stamp the claim | `channel://…` or `file://…` |

The author writes one verb regardless of value type. The framework picks the store; the URI scheme of `out_<name>` is the consumer's type indicator at read time.

### `stream` and `observe` are strictly orthogonal

`stream(name, sample)` writes to a channel — period. It **never** stamps `out_*`. It **never** auto-associates with a vector.

`observe(name, channel_handle)` is how you associate a channel with a vector. Always explicit, every time you want the association.

Why no auto-association: streams legitimately span vectors (background recorders, fixture loggers, multi-run captures). Auto-association on first write would surprise in those cases. Verbosity is cheap; surprise behavior is expensive.

```python
# Vector-scoped streaming — two lines
ch = channels.stream("iv_curve.i")
observe("iv_curve.i", ch)                    # explicit link to this vector
for v in voltages:
    psu.set_voltage(v)
    stream("iv_curve.i", dmm.read_current())

# Session-scoped background recording — no observe = no vector association
def session_setup():
    return channels.stream("operator_camera")

def test_thing(camera):
    observe("operator_camera", camera)        # this test claims it
    verify("vout", dmm.read(), Limit(low=3.2, high=3.4))

def test_other(camera):
    # This test doesn't observe the camera — not associated with this test's rows
    verify("vout", dmm.read(), Limit(low=3.2, high=3.4))
```

### Why streaming is explicit per store (not polymorphic)

`observe` / `verify` are **intent verbs** — author says "associate this," framework figures out where. Polymorphic dispatch fits.

`stream` / `filestore.stream` are **operational verbs** — author is actively managing a write with concerns about buffering, subscription, throughput, lifecycle. The operational shapes are too different to share one verb:

| | Channel stream | File stream |
|---|---|---|
| Granularity | one typed sample per call | bytes per chunk |
| Lifecycle | append-only, no close per sample | open / write / close |
| Live subscribers see | each row via Flight `do_get` | partial bytes via HTTP range + frame-index events |
| Throughput model | per-sample notify, segment-rotated files | byte-aware buffering, format-specific encoder |

So polymorphic dispatch lives in intent verbs; operational verbs are explicit per store.

---

## 4. When to use what — decision matrix

The synthesis. Pick the row that matches your intent; everything else follows.

| You want to… | Verb | Value type | Lands in | Row in parquet? |
|---|---|---|---|---|
| Judge a scalar against limits | `verify(name, v, limit=L)` | `float`/`int`/`bool` | event payload | judged row |
| Record a scalar with no judgment (characterization) | `verify(name, v)` no limit | scalar | event payload | DONE row |
| Stamp contextual scalar on the vector (DUT temp, operator, supply) | `observe(name, v)` | scalar | `out_<name>` inline | auto-promote if vector has no `verify`; else rides along |
| Capture a discrete waveform | `observe(name, wf)` | `Waveform` / `ndarray` | ChannelStore (one row) | `out_<name> = channel://…`; auto-promote rule applies |
| Capture a discrete file artifact (image, vendor blob, file on disk) | `observe(name, X)` | `Path` / `bytes` / `PIL.Image` / etc. | FileStore (one file) | `out_<name> = file://…`; auto-promote rule applies |
| Force a first-class row for an artifact | `verify(name, X)` no limit | non-scalar | ChannelStore or FileStore (by shape) | explicit DONE row |
| Judge a derived stat from a captured artifact | `verify(name, derived, limit=L)` | scalar | event payload | judged row; sees source URI via `out_*` on the same vector |
| Stream continuous numerics (sweep, derived signal) | `stream(name, sample)` per sample | scalar | ChannelStore (one row per sample) | no row; needs explicit `observe(name, handle)` to associate |
| Stream continuous bytes to one file (video, large capture) | `with filestore.stream(name, format=…) as sink:` | bytes per `sink.write(chunk)` | FileStore (one file, incremental) | no row; needs explicit `observe(name, sink)` to associate |
| Reference existing channel/file by URI or handle | `observe(name, handle_or_URI)` | handle / URI / Path-already-in-store | (no re-write) | claim stamped on `out_<name>` |

### Quick decision tree

```
Do you want to judge a value against a limit?
├── yes → verify(name, scalar, limit=L)
└── no
    │
    Do you need a measurement row in parquet?
    ├── yes → verify(name, value)            (no limit; any shape OK)
    └── no
        Is it streaming (continuous samples)?
        ├── numerics    → stream(name, sample) + observe(name, ch) to associate
        ├── bytes/video → filestore.stream(...) + observe(name, sink) to associate
        └── one-shot    → observe(name, value)
```

### What test authors actually write (the 95% case)

```python
# Scalar judgment (the most common thing in a test)
verify("vout", dmm.measure_voltage(), Limit(low=3.2, high=3.4))

# Scalar characterization (no spec yet)
verify("ambient_temp", thermometer.read())

# Vector context (lands on every measurement row's out_*)
observe("operator_id",    "ALICE")
observe("supply_voltage", psu.measure_voltage())

# Discrete waveform (channel-shaped → ChannelStore)
observe("scope.ch1.capture", scope.acquire())

# Discrete artifact (file-shaped → FileStore)
observe("front_panel_photo", camera.snap())
observe("nicom_dump",        Path("dut.tdms"))

# Derived stats from a captured waveform (judged scalars; share out_ source)
wf = scope.acquire()
observe("scope.ch1.capture", wf)
verify("overshoot", overshoot(wf), Limit(low=0, high=0.5))
verify("max",       wf.max(),       Limit(low=3.2, high=3.4))

# Continuous channels happen automatically when the test uses an instrument
# whose driver internally calls observer.read() — author does not call
# ChannelStore directly.

# Streaming captures (video / continuous DAQ to file) — opt-in API
with filestore.stream("dut_video", format="mp4") as sink:
    camera.stream_to(sink)
observe("dut_video", sink)        # link to this vector
```

### What test authors NEVER write

- `ChannelStore.write(...)` directly — go through `stream`, `channels.write`, or use an instrument (observer.read).
- `FileStore.put(...)` raw — go through `observe`/`verify` (for one-shot), `filestore.put` (for explicit one-shot artifact), or `filestore.stream` (for incremental bytes).
- `EventLog.emit(...)` directly — events are emitted by the verbs and lifecycle machinery.
- Format-specific serializer calls (`.npz`/`.png`/`.mp4`) — pass the Python object; the registry picks the format.

### Related and composite data — XY, complex, paired streams

A common T&M shape is **two related arrays/streams** — IV curves (V + I), S-parameters (real + imag), eye diagrams (time-offset + voltage), spectrum analyses (frequency + magnitude). The architecture handles these without adding store complexity. Two patterns, picked by use case:

**Pattern A — Two related channels (for streaming, live UI).** When the data is continuous over time and you want it live-subscribable, use two channels with a **shared prefix**:

```python
# Streaming IV sweep over time
stream("iv_sweep.voltage", psu.measure_voltage())
stream("iv_sweep.current", dmm.measure_current())

# Streaming complex S11 (real + imag as paired channels)
stream("s11.real", complex_val.real)
stream("s11.imag", complex_val.imag)
```

Relationship is preserved by **naming convention** + **shared session/timestamps**. Reader correlates by timestamp join (in close-timing acquisitions, exact match; otherwise within ε). Each axis stays independently live-subscribable — UIs can plot voltage and current side-by-side, or compute current/voltage live.

**Pattern B — One discrete artifact (for captures).** When the data is a complete dataset captured at one moment (one IV curve per test, one full S-parameter sweep), use a Pydantic model or numpy array. The serialization registry routes it to FileStore:

```python
# IV curve as a discrete artifact per vector
xy = XYData(x=voltages, y=currents, x_units="V", y_units="A")
observe("iv_curve", xy)                   # → FileStore as .npz

# Complex sweep — numpy complex128 is a primitive dtype
s_params = np.array([0.5+0.3j, 0.4+0.2j, ...], dtype=np.complex128)
observe("s11_sweep", s_params)            # → FileStore as .npy (round-trips losslessly)

# Eye diagram XY scatter
observe("eye_diagram", XYData(x=time_offsets, y=voltage_samples))
```

`XYData` is a small Pydantic model (`x`, `y`, optional `x_units`/`y_units`/`x_name`/`y_name`) — see build item 15. Numpy `complex64`/`complex128` are first-class dtypes that round-trip through `np.save` cleanly; the existing serialization registry handles them without special-casing.

**When to use which:**

| Your data | Pattern |
|---|---|
| Streaming pairs over time, live UI watchability | A — two related channels |
| One discrete dataset per vector / per acquisition | B — model / numpy → FileStore |
| Frequency-domain captures (FFT, S-parameters, spectrum) | B — complex numpy → FileStore |
| Eye diagrams, scatter plots | B — `XYData` → FileStore |
| Real-time correlated streams from two instruments | A — two channels |
| Pre-computed lookup tables / calibration curves | B — model / numpy → FileStore (or station YAML if static) |

**Why not add struct/composite types to ChannelStore?** Two parallel channels preserve independent live subscribability AND keep ChannelStore's schema simple ("typed scalars and arrays of primitives" — see build item 14). Forcing per-row structs would mean every consumer needs per-channel schema awareness for the composite case. Two channels are simpler to subscribe to, aggregate, and reason about. Composite-as-one-artifact belongs in FileStore.

**Industry precedent** — same shape choices:

| System | Pattern |
|---|---|
| TDMS | Group with separate channels for related signals ("IV_Sweep" group containing "Voltage" + "Current"); OR composite waveforms in one channel |
| HDF5 / NWB | Dimension-scale-paired datasets for XY; native `complex64`/`complex128` dtypes |
| TouchStone (VNA) | Parallel columns per S-parameter (S11_real, S11_imag, …) |
| NumPy | Native `complex64`/`complex128`; `np.savez` for paired x/y arrays |

---

## 5. ChannelStore — schema details

### Two row shapes, same column conventions

```python
SCALAR_SCHEMA = pa.schema([
    ("acquired_at", pa.timestamp("us", tz="UTC"), nullable=True),   # instrument's clock when provided
    ("received_at", pa.timestamp("us", tz="UTC")),                  # platform wall-clock at write
    ("value",         pa.float64()),
    ("source_method", pa.utf8()),
    ("session_id",    pa.utf8()),
])

ARRAY_SCHEMA = pa.schema([
    ("acquired_at",     pa.timestamp("us", tz="UTC"), nullable=True),   # instrument's t0 (Waveform.t0 lands here)
    ("received_at",     pa.timestamp("us", tz="UTC")),                   # platform wall-clock at write
    ("samples",         pa.list_(pa.float64())),
    ("sample_interval", pa.float64()),                                   # per-acquisition spacing (dt)
    ("source_method",   pa.utf8()),
    ("session_id",      pa.utf8()),
])
```

Two timestamps per row, distinct meanings:

| Column | Meaning | Source | Presence |
|---|---|---|---|
| `acquired_at` | when the data was acquired on the instrument's clock | instrument-provided when available; null otherwise | nullable |
| `received_at` | when the row was written to ChannelStore | always platform wall-clock | required |

When the producer doesn't provide an instrument timestamp, `acquired_at` is null. When provided, the gap (`received_at - acquired_at`) gives transport latency / acquisition staleness — useful for clock-drift detection and audit.

### Supported leaf types (v0.2.0)

ChannelStore is **a typed time-series store for any primitive leaf type, in scalar or array shape**. Build item 14 closes today's gaps (scalar `int` cast to float; arrays hardcoded to float64). After v0.2.0:

| Shape | Supported leaf types | `ChannelDescriptor.data_type` |
|---|---|---|
| Scalar | `float`, `int`, `bool`, `str` | `"scalar:float"`, `"scalar:int"`, `"scalar:bool"`, `"scalar:str"` |
| Array | `list<float>`, `list<int>`, `list<bool>`, `list<str>`, numpy ndarrays of any primitive dtype | `"array:float"`, `"array:int"`, `"array:bool"`, `"array:str"` |

The leaf type is **inferred at first write** (kind-registry pattern, same as `units`) and validated on subsequent writes. Mismatches error at write time.

Use cases for non-float channels:
- **Digital waveforms** — `list<bool>` (logic-analyzer trace, GPIO state stream)
- **Status / state streams** — `scalar:str` (operator status, state machine label)
- **Error code streams** — `scalar:int` (counter values, error codes as integers)
- **Boolean indicators** — `scalar:bool` (fault active, lid open, ready signal)
- **Counter values** — `scalar:int` (preserves int semantics; no float-truncation hazard)

ChannelStore explicitly **does not** support composite/struct values per row (no `pa.struct<...>`, no native complex). For paired streams (XY, complex), use two related channels (Pattern A in §4). For composite captures, use `XYData` / numpy → FileStore (Pattern B in §4). This keeps ChannelStore simple and uniformly typed.

### Channel descriptor (kind registry)

`ChannelDescriptor` (`models.py:19-28`) is **global, kind-level**, no `session_id`, no `run_id`:

```
channel_id, data_type, instrument_role, resource, units, attributes (dict), first_seen
```

`attributes` is the dict for project-specific metadata (channel-level). **No promoted typed fields for `sample_rate` / `dt`** — those are waveform-specific concepts and live on the Waveform model. The descriptor uniformly serves any channel shape. *(Today this field is `properties` in source; rename to `attributes` is build item 17 — pre-1.0 mechanical alignment with `FileArtifactMetadata.attributes` and `Waveform.attributes`.)*

**Channel kind is global per `channel_id`, NOT per session.** One descriptor per `channel_id` across all sessions. The kind-registry (`_registry.json`) is session-agnostic. So `data_type`, `units`, `instrument_role`, and (after build item 14) the leaf type are pinned at first write and validated on every subsequent write, **across all sessions ever**. Two sessions cannot have the same `channel_id` with different leaf types — the second one errors. This is intentional: cross-session analytics need stable schemas per channel.

### Naming conventions and uniqueness

**`channel_id` is the global identifier**; uniqueness is the author's responsibility. If two unrelated producers both write `channel_id="voltage"`, they share the same descriptor and the same channel data rows (distinguished only by `session_id` per row). After build item 14, type mismatch errors; same type silently merges.

This is a real namespace-collision risk for test authors. **The established convention is dot-separated namespacing**:

| Naming pattern | Example | When to use |
|---|---|---|
| `{instrument_role}.{signal}` | `dmm.voltage`, `psu.voltage`, `scope.ch1` | driver-produced channels (most common — `observer.read` auto-prepends the instrument role) |
| `{instrument_role}.{port}.{aspect}` | `scope.ch1.capture`, `scope.ch2.capture` | multi-port instruments where each port is a logical channel |
| `{component}.{signal}` | `fixture.lid_open`, `chamber.temp` | non-instrument continuous streams (fixture sensors, environmental) |
| `{purpose}.{signal}` | `iv_sweep.voltage`, `iv_sweep.current` | paired streams from a single test phase (per §4 Pattern A) |
| `{namespace}.{leaf}` | anything you compose | escape hatch for project-specific naming |

**Drivers handle namespacing automatically.** `observer.read(channel="voltage", value=v, method="measure_dc_voltage")` from inside a driver whose `instrument_role="dmm"` writes to `channel_id="dmm.voltage"` — the role is the namespace. So driver authors don't think about collision; the wrapper handles it.

**Test-author `observe`/`stream` calls need explicit naming.** When the test author writes `observe("voltage", v)`, there's no implicit namespace; collision is possible. Either:

```python
# Author types the namespace explicitly
observe("psu_under_test.voltage", v)

# OR (build item 16, future) — explicit namespace argument
observe("voltage", v, namespace="psu_under_test")    # → "psu_under_test.voltage"
```

**Multi-DUT parallel execution is NOT a collision problem.** Each slot/worker emits its own `SessionStarted` (per `slot_runner.py:568`), so all writes share the channel kind but are session-tagged on the data rows. Same `channel_id="dmm.voltage"` across 4 workers means: one shared descriptor (correct — it IS the same kind of signal), four distinct sessions of data rows (correct — per-DUT isolation). The kind-registry validates that all workers are writing the same type — which is what you want.

**What the system does NOT enforce:**
- No format requirement (you CAN write a bare `channel_id="voltage"`; system accepts it)
- No reservation registry (anyone can claim any name first)
- No auto-prefix outside `observer.read` (the test-author `observe` path is honest about what name the author typed)

**Recommended discipline:**
1. Drivers always namespace by `instrument_role` (handled automatically by the observer wrapper).
2. Test authors namespace by purpose, fixture, or DUT-context — never bare leaf names like `"voltage"` unless you can guarantee uniqueness across all tests in the project.
3. If you hit a kind-registry collision error, that's the system telling you two unrelated producers grabbed the same name — disambiguate by renaming, not by deleting the descriptor.

For edge cases (intentional schema migration, instrument swap): a `litmus channels reset-descriptor <channel_id>` admin tool would handle the rare cases. Not v0.2.0 critical; v0.2.x patch.

### Scoping reality

| Scope | Where it lives for a channel |
|---|---|
| Kind (what is `scope.ch1`) | global `_registry.json` descriptor — session-agnostic |
| Session (this occurrence) | data rows (`session_id` col) + filename (`_{session_short}.arrow`) + the claim URI |
| Run (was it part of a run) | **not stamped on the channel** — carried by the session-bearing claim URI flowing into events + parquet |

**Run association is mediated, not direct.** Channels legitimately span runs (fixture-temp logger across run 1, 2, 3); they exist outside runs (calibration, idle monitoring). Forcing `run_id` onto the channel would require duplicating data per run, arbitrarily picking one run, or carrying null. The materializer's `find_channel_refs` resolves "channels in run X" via the runs DuckDB index efficiently — no need for direct stamping.

**The claim URI is session-bearing.** `make_channel_uri` (`ref.py:42`) emits `channel://{channel_id}?session={session_id}`. That URI rides from the channel write into the `InstrumentRead` event payload and into the parquet `out_*` column. A run "has" a channel by *referencing its session-scoped URI*, not by tagging the channel with a run id.

### How Waveforms land in ChannelStore

```
wf.Y               → samples column
wf.t0              → acquired_at column (instrument's clock for the first sample)
wf.dt              → sample_interval column (per-acquisition spacing)
wf.attributes      → descriptor.attributes (channel-level) or row-inlined (per-acquisition); TBD
platform wall-clock → received_at column
```

So Waveform's domain vocabulary (`t0`, `dt`, `attrs`) round-trips through ChannelStore's universal schema without polluting it. The Waveform model carries `t0`/`dt`/`attrs` as its own internal terms; the store uses `received_at`/`acquired_at`/`sample_interval` consistently.

---

## 6. FileStore — typing and library reuse

### MIME as primary identifier

Artifact metadata:

```python
class FileArtifactMetadata:
    file_uri: str                  # "file://_ref/dut_video.mp4"
    mime_type: str                 # "video/mp4" — primary dispatch field
    extension: str                 # ".mp4" — fallback when MIME is ambiguous
    size: int
    session_id: str
    created_at: datetime
    original_filename: str | None  # preserved on Path-copy
    attributes: dict[str, Any]     # format-specific extras: width/height, duration, codec, …
```

Standard IANA types cover most cases. T&M-specific formats need a small Litmus convention table for vendor types without registered MIMEs:

| Format | MIME |
|---|---|
| PNG, JPEG, MP4, WebM, PDF, JSON, CSV, Parquet | standard IANA |
| NumPy `.npz` | `application/x-numpy-archive` (de-facto) |
| NumPy `.npy` | `application/x-numpy` |
| TDMS | `application/vnd.ni.tdms` |
| Pickle (fallback) | `application/x-python-pickle` |

### Display dispatch by MIME family

```python
def render(artifact):
    mime = artifact.mime_type
    if mime.startswith("image/"):                       return ImageRenderer(artifact)
    if mime.startswith("video/"):                       return VideoPlayer(artifact)
    if mime.startswith("audio/"):                       return AudioPlayer(artifact)
    if mime == "application/pdf":                       return PDFViewer(artifact)
    if mime == "application/x-numpy-archive":           return WaveformPlotter(artifact)
    if mime.startswith("text/") or mime.endswith("json"): return TextViewer(artifact)
    # … else: DownloadOnly
```

### Reuse format libraries — FileStore is orchestration only

FileStore owns **artifact identity** (URI, location, metadata, lifecycle events). Format **encoding/decoding** belongs to existing well-tested libraries. The serializer registry exposes two interfaces per format:

```python
class FormatHandler(Protocol):
    def put(self, value: Any, dest: Path) -> Path: ...                 # one-shot
    def open_writer(self, dest: Path, **opts) -> StreamingSink: ...    # streaming
    def detect_attributes(self, dest: Path) -> dict[str, Any]: ...     # metadata at close
```

Recommended wrappers:

| Format | Library | Notes |
|---|---|---|
| MP4 / H.264 / WebM | **PyAV** (ffmpeg bindings) | full codec coverage; streaming-friendly |
| WAV / FLAC / OGG | **soundfile** (libsndfile) | scientific/audio standard |
| Multi-frame TIFF | **tifffile** | BigTIFF, OME-TIFF |
| PNG / GIF / animated PNG | **Pillow** | already in registry |
| TDMS | **nptdms** | `TdmsWriter` is chunk-friendly |
| HDF5 | **h5py** | chunked datasets |
| Parquet | **pyarrow** | already in stack |
| Pickle (fallback only) | stdlib | emits `RuntimeWarning` naming the type |

FileStore handles path allocation, lifecycle events (`StreamStarted`/`StreamFrameIndex`/`StreamEnded`), metadata capture at close, claim URI generation. The library handles encoding. **Don't reinvent encoders.**

### Live read of FileStore streams

FileStore streaming sinks support **live read during write** — not just final-artifact-on-close. The mechanism:

```
Producer:                              Live consumer:
─────────                              ──────────────
filestore.stream(name, format)
  → StreamStarted(path, …)        →   learn path from event
sink.write(chunk1)
  → StreamFrameIndex(offset=N1)   →   range-read 0..N1 (HTTP Range header); decode; render
sink.write(chunk2)
  → StreamFrameIndex(offset=N2)   →   range-read N1..N2; decode; render
…
sink.close()
  → StreamEnded(file://…)         →   final URI; complete file
```

Live consumers don't poll — they react to `StreamFrameIndex` events and range-read the new byte range. Standard HTTP `Range: bytes=N1-N2` requests; the `artifact_viewer.py` endpoint already supports this.

**Format friendliness for partial reads** varies — choose accordingly when live-read-during-write is wanted:

| Format | Partial read during write? |
|---|---|
| Fragmented MP4 (fMP4) | yes — moov at start, mdat fragments append-only |
| HLS / DASH segments | yes — each segment is a complete small file |
| WAV (uncompressed PCM) | yes — header at start, samples append-only |
| Raw bytes (`.bin`, line-delimited JSON) | yes — append-only |
| HDF5 / TDMS | yes — append-friendly by design |
| Regular MP4 | **no** — moov often at end; partial file unplayable |
| PNG / JPEG | partial — progressive forms only |
| Pickle / NPZ | **no** — written all-at-once or unreadable |

Default to streaming-friendly formats (fMP4 for video, WAV for audio, HDF5/TDMS for typed data) when live read matters. One-shot puts via `filestore.put` bypass this concern — the file appears atomically when the put completes (write to temp + rename).

So both stores support live read; the protocol and shape differ:

| | ChannelStore | FileStore (stream) |
|---|---|---|
| Live read protocol | Flight `do_get` — typed rows pushed | HTTP range read + `StreamFrameIndex` events |
| What you get | typed numeric values (samples) | raw bytes — consumer decodes via format library |
| Per-sample query (at rest) | yes — directly via SQL on typed columns | no — open file, parse format, then filter |
| Format dependency | none — values are typed | strong — consumer needs format-aware decoder |
| Best for | high-rate typed numerics, plotting, statistics | continuous bytes producing one meaningful artifact (video, audio, log) |

---

## 7. Manifestation rules — observations, verifies, and rows

Where data is **stored** is separate from how it **manifests** as parquet rows at materialization. The two are orthogonal.

### Vector grain

- `out_<name>` lives on the **vector**, not the row.
- At materialization, every measurement row in a vector denormalizes the full `out_*` map onto itself. Multiple rows in one vector share the same `out_*` columns.
- **Last-write-wins** for repeated stamps of the same name within a vector. `out_<name>` is a snapshot, not a history.

### Type stability per name (kind-registry pattern)

`out_<name>` must be type-stable across vectors and runs — otherwise the parquet column has two types and the materializer must coerce or refuse. Mirrors ChannelStore's `_registry.json`: first observation of a name registers the kind (scalar-typed column, `file://` URI column, etc.); subsequent observations must match. Mismatches error at materialization.

### Row emission policy (auto-promotion)

For each vector, at materialization:

| Vector contained | Row emission |
|---|---|
| **≥1 `verify`** | the verify rows are it. Observations ride along as `out_*` columns on every row via denormalization. **No DONE row per observation.** |
| **0 `verify`, ≥1 `observe`** | each observation **promotes to a DONE row** — `name = observation_name`, `value = NULL`, `outcome = DONE`, full vector `out_*` denormalized. |
| **0 of either** | no row. Empty vector. |

The decision is **materialization-time**, not eager. Events stream as they happen; the materializer reads the per-vector tally to decide row emission.

### Verbs are separate

`verify` is **judgment-bearing**: it judges a numeric scalar against a limit. It is **not** the polymorphic data-routing verb. That role belongs to `observe`. Pass a non-scalar to `verify` and you get a `TypeError` pointing you at `observe`.

The right pattern when you want both an artifact captured AND a metric judged is two verbs in the same step:

```python
observe("scope.ch1.capture", wf)                      # URI on out_*
verify("overshoot", overshoot(wf), Limit(low=0, high=0.5))
```

The captured artifact is queryable from any row in the vector via `WHERE out_scope.ch1.capture IS NOT NULL` — by data presence, not row name.

### Cost worth naming

When a test evolves from pure characterization → mixed (author adds the first `verify`), the DONE rows from observations **disappear in new runs**. Old runs still have them; new runs don't. Consistent with the principle — observations are vector *context*; rows are for *measured* things — but it means cross-version queries by `measurement_name` won't see the captures in v2. The reliable cross-version query is `WHERE out_<name> IS NOT NULL` — ask by *data presence*, not *row name*.

---

## 8. Events as the spine

Every meaningful operation emits an event; each carries data inline (when small) or a **claim URI** (when not). Every consumer — live or at rest — reaches data by walking events and following claims.

### What gets emitted, what gets stored where

| Operation | Event emitted | Data lands in | Event payload carries |
|---|---|---|---|
| `observe(name, scalar)` | `Observation` *(needs adding — silent today)* | event itself | `name`, `value` inline |
| `observe(name, channel-shaped)` | `Observation` + `ChannelStarted` *(first write per channel/session only)* | ChannelStore (one row per call) | `Observation`: `name`, `channel://…` claim. `ChannelStarted`: see below |
| `observe(name, file-shaped)` | `Observation` *(needs adding)* | FileStore | `name`, `file://…` claim, mime/dtype attrs |
| `verify(name, scalar, limit)` | `Measurement` | event itself | `name`, `value`, `limit`, `outcome` |
| `verify(name, non_scalar)` | `Measurement` | ChannelStore or FileStore (by shape) | `name`, `value=NULL`, `outcome=DONE`, claim URI |
| `stream(name, sample)` | `ChannelStarted` (first write per channel/session only) | ChannelStore (one row per call) | **NO per-sample event** — sample data via ChannelStore subscription only |
| `channels.write(name, sample)` | `ChannelStarted` (first write per channel/session only) | ChannelStore (one row per call) | same — no per-sample event |
| `observer.read(...)` (driver) | `ChannelStarted` (first write per channel/session only) + (existing) `InstrumentConnected` carries instrument identity at connect time | ChannelStore (one row per call) | same — no per-sample event |
| `filestore.stream(name, format)` | `StreamStarted` / `StreamFrameIndex` ×N / `StreamEnded` | FileStore (one file, incremental) | `stream_id`, `format`, `path`, final `file://…` in `StreamEnded` |
| Channel pruned (retention) | `ChannelClosed` | — | `channel_id`, `session_id`, `reason` |
| Run / step / vector lifecycle | `RunStarted`, `StepStarted`, `VectorStarted`, `VectorEnded`, `StepEnded`, `RunEnded` | event itself | identifiers + timestamps |

### Channel events are lifecycle-only, not per-sample

> **Architectural call (Position 2 — adopted for v0.2.0):** the EventStore is the timeline of *significant moments*; channel sample data is NOT a stream of timeline events. Per-sample channel writes go to ChannelStore only. Subscribers wanting per-sample data subscribe to ChannelStore via Flight `do_get`; subscribers wanting the timeline subscribe to EventStore.

This matches the OTel-shaped split (traces ≠ metric samples). Reasons:

- **Scaling.** Channels can write at 10 kHz+; per-sample events flood the EventStore. The event log was the dominant write-side cost and the JSON-payload-filter the dominant read-side cost. Lifecycle-only makes both vanish for sample data.
- **Honesty.** Per-sample writes aren't "things that happened" at the timeline level — they're continuous data. Mixing them with run/step/vector lifecycle events makes the timeline harder to scan for significant moments.
- **No data loss.** Sample data is still complete in ChannelStore (every sample is a row). The event log just isn't the place to consume it.
- **Tool affinity.** Per-sample subscribers already want ChannelStore semantics (typed rows, time-window queries, per-sample notify). Forcing them through the event log was the wrong shape.

**What lifecycle events for channels actually carry:**

```python
class ChannelStarted(EventBase):
    """First sample written to a channel in this session.

    Once per (channel_id, session_id). Carries enough context to
    let consumers find + subscribe to the channel without further
    discovery.
    """
    event_type: Literal["channel.started"] = "channel.started"
    channel_id: str
    data_type: str              # "scalar:float", "array:bool", etc. (per item 14)
    units: str | None = None
    # Instrument fields — populated when source is an instrument (observer.read);
    # null for stream() / channels.write / daemon-driven writes.
    instrument_role: str | None = None
    method: str | None = None
    resource: str | None = None

class ChannelClosed(EventBase):
    """Channel reached end of session OR was retention-pruned.

    Once per (channel_id, session_id). Useful for consumers tracking
    "channel is still being written to" vs "no more data coming".
    """
    event_type: Literal["channel.closed"] = "channel.closed"
    channel_id: str
    reason: str  # "session_ended" | "retention_prune" | etc.
```

**What goes away under Position 2:**

- `InstrumentRead` as a per-sample event — retired in v0.2.0. Its instrument-identity payload moves to `ChannelStarted` (instrument fields, populated when applicable) + the existing `InstrumentConnected` (fires once per instrument per session, already carries `manufacturer` / `model` / `serial` / `firmware` / `cal_*`).
- No new `ChannelSample` per-sample event. Sample data is reachable via ChannelStore queries / subscriptions; the event log doesn't duplicate it.

**What this means for consumers:**

- "Live plot of channel X" → subscribe to ChannelStore Flight `do_get` (per-sample push). EventStore subscription only for "channel X started" / "channel X closed" notifications.
- "What was happening in this run?" → walk EventStore for the significant moments; correlate to ChannelStore queries (by session_id + time bounds) for sample data when needed.
- "Audit trail of every read" → ChannelStore IS the audit trail (every sample is a row, immutable, session-stamped). No need for per-sample events.

### Hierarchical context — what each level's lifecycle event snapshots

The four-level hierarchy (session ⊃ run ⊃ step ⊃ vector) carries traceability context in **layered snapshots** at each grain. The materializer flattens all four onto every measurement row so analytic queries don't need joins.

| Level | Event | Snapshots | Source |
|---|---|---|---|
| Session | `SessionStarted` (`events.py:60-133`) | station identity, operator, fixture, slot count, process | once at session open |
| Run | `RunStarted` (`events.py:154-203`) | DUT (serial, part, revision, lot), part, git state, project, environment fingerprint, custom metadata (+ station fields duplicated for self-contained query) | once per run within a session |
| Step | `StepStarted` | step path + module/function context | once per test method |
| Vector | `VectorStarted` | `in_*` / conditions for this acquisition | once per parametrize/sweep iteration |

**`SessionStarted` carries** (verified at `events.py:60-133`):

| Layer | Fields |
|---|---|
| Station identity | `station_id`, `station_name`, `station_type`, `station_location`, `station_hostname` |
| Operator | `operator_id`, `operator_name` |
| Fixture | `fixture_id`, `slot_count` |
| Process | `pid`, `client` (auto-detected) |
| Type | `session_type` (default `"test_run"`) |

`SessionStarted` explicitly **rejects `run_id`** (validator at `events.py:89-93`) — runs come later.

**`RunStarted` carries** (verified at `events.py:154-203`):

| Layer | Fields |
|---|---|
| Station (duplicated from session for self-contained query) | `station_id`, `station_name`, `station_type`, `station_location`, `station_hostname`, `slot_id`, `slot_index` |
| DUT | `dut_serial`, `dut_part_number`, `dut_revision`, `dut_lot_number` |
| Part | `part_id`, `part_name`, `part_revision` |
| Operator (duplicated) | `operator_id`, `operator_name` |
| Test context | `fixture_id`, `test_phase`, `project_name`, `git_commit`, `git_branch`, `git_remote` |
| Environment snapshot | `environment_json` (python / litmus versions + fingerprint) |
| Extension | `custom_metadata: dict` |

The duplication of station fields on `RunStarted` is intentional: makes the event self-contained for query without forcing a session-join.

### Auto-populated vs configured fields

The factory `SessionStarted.from_station(...)` (`events.py:95-133`) and the `RunStarted` constructor split fields by source:

**Auto-populated** (no caller input needed):
- `station_hostname` ← `socket.gethostname()`
- `pid` ← `os.getpid()`
- `client` ← `_detect_client()` (introspects calling context)
- `slot_count` ← `_LITMUS_SLOT_COUNT` env var (default 1)
- `environment_json` ← python / litmus versions + fingerprint
- `git_commit`, `git_branch`, `git_remote` ← detected from project's git working tree

**Configured** (caller must provide; sourced from project YAML or test context):
- `station_id`, `station_name`, `station_type`, `station_location` — from station YAML
- `operator_id`, `operator_name` — explicit at session open
- `dut_serial`, `dut_part_number`, etc. — from test setup
- `part_id`, `part_name`, `part_revision` — from station's part config
- `fixture_id` — from fixture YAML
- `custom_metadata` — explicit

If config is sparse (bringup tier — no station YAML), the configured fields are `None` (allowed by the model). The event still fires; the metadata is just sparser.

### Hardware identity — `InstrumentConnected` family

Per-instrument identity isn't on `SessionStarted` (instruments connect lazily, after session open). It lives in per-instrument events emitted as each instrument connects:

**`InstrumentConnected`** (`events.py:290-308`):

| Field | What it captures |
|---|---|
| `role` | logical role (`"scope"`, `"dmm"`) |
| `instrument_id` | logical id from project config |
| `driver` | which driver class is wrapping it |
| `resource` | physical connection (VISA / hostname / etc.) |
| `protocol` | `"visa"`, `"lxi"`, etc. |
| `manufacturer`, `model`, `serial`, `firmware` | identity from `*IDN?` response |
| `cal_due`, `cal_last`, `cal_certificate`, `cal_lab` | calibration cert state |
| `mocked` | whether this is a mock instrument |

Plus `IdentityVerified` (`events.py:311-317`) — expected-vs-actual identity check (`matches`, `mismatches`); and `CalibrationWarning` (`events.py:320-325`) — fires when cal cert is approaching expiry.

So the full chain for "what hardware produced channel X in run Y":

```
measurement row (parquet)
  → run_id
  → events for that run_id (event log)
  → out_<channel_name> claim URI on measurement → channel_id + session_id
  → InstrumentConnected event for that session + instrument_role
  → manufacturer + model + serial + firmware + cal_certificate
```

Multi-step join, but every layer is captured automatically when the instrument connects. The test author / driver author doesn't have to manually log "I'm using DMM serial 12345" — that's the wrapper's job. **Hardware-traceability evidence is intrinsic, not an add-on.**

### Static identity vs continuous drift — the split

Lifecycle events (`SessionStarted`, `RunStarted`, `InstrumentConnected`) are **snapshots at known moments**. They don't track environmental drift during the session.

| Concern | Where it lives |
|---|---|
| Static station identity (hostname, fixture, operator login) | `SessionStarted` |
| Per-DUT swap (serial, part, git state) | `RunStarted` |
| Static instrument identity (model, serial, cal cert at connect) | `InstrumentConnected` |
| **Continuous environmental drift** (lab temp over 4-hour run, line voltage variation) | **ChannelStore** via `observer.read` from an environmental driver, or `stream(...)` from non-instrument sources |
| Discrete artifacts during the session (operator photo, vendor file capture) | FileStore via `observe(name, file/image)` |

So mid-session drift uses the **data layer** (channels stream environmental sensor readings; runs query by URI + time bounds to find "what was lab temp at this measurement's time"). Lifecycle events stay for identity at known moments.

### Session entry points — factory-mediated consistency

`SessionStarted` is currently emitted from three places, all via the same `from_station(...)` factory:

| Caller | Where | Purpose |
|---|---|---|
| Pytest plugin | `pytest_plugin/__init__.py:269` | test orchestrator opens a session |
| Interactive `connect.py` | `connect.py:105` | scripts / manual runs via `litmus.connect(...)` |
| Multi-DUT slot runner | `execution/slot_runner.py:568` | parallel DUT execution |

The factory enforces the auto-detection behavior (hostname, pid, client, slot_count). Three callers, one factory, identical session-open semantics. A `test_conventions.py`-style guard could enforce going forward: "all `SessionStarted` construction goes through `from_station`."

Other references to `SessionStarted` in source are all **consumers** (read for display / pid tracking / list-known-sessions API / MCP tool surface) — write-side stays in the three known places.

### UI as a fourth entry point (future)

A UI is a natural fourth caller, in two modes:

**UI opens a session.** Operator clicks "Start session" → UI process emits `SessionStarted.from_station(...)` (same factory) with config it gathered from dropdowns / operator login / fixture barcode scan. Subsequent activity within the session (manual measurements, scripted runs launched from the UI) shares the `session_id`. The UI becomes a fourth caller of the factory; no new mechanism needed.

**UI joins an active session.** UI doesn't emit — just subscribes. Discovers the `session_id` via:
- Session-list endpoint (`event_store.list_known_sessions()` is already exposed via MCP and used by UI components)
- URL with `session_id`
- "currently active on this bench" lookup (sessions where `SessionEnded` hasn't fired)

Then opens an event subscription filtered by `session_id` — the consumer SDK shape from §9: `LiveClient.subscribe_events(EventFilter(session_id=…))`. Receives all events as the bench produces them; follows claim URIs to ChannelStore / FileStore for data; renders.

Neither mode needs new architecture — both ride the existing factory + consumer SDK. The work to enable UI session-create is UX (gather config from the operator); the work to enable UI session-join is the Consumer SDK (build item 20).

### Three lifecycle phases — same events, three roles

**Phase 1 — Live (during the run).** Subscribers (UIs, MCP tools, log tailers) connect to the event stream and react in real time. A channel-detail UI receives an `InstrumentRead` → reads the `channel://` URI → queries / subscribes via Flight `do_get` → re-renders the plot. A file-detail UI receives an `Observation` with `file://…` → reads the artifact via the FileStore endpoint. A stream watcher subscribes to `StreamFrameIndex` → reads partial bytes from the still-being-written file. **The event tells you what and where; the store tells you the bytes.**

**Phase 2 — Materialization (at `RunEnded`).** The materializer walks the event log for the run and builds the parquet measurement table. `Measurement` events → rows. `Observation` events in the same vector → `out_*` columns denormalized onto every measurement row. The auto-promotion rule decides whether observations also become DONE rows. Claim URIs flow through as-is into the parquet columns. `materialize_channel_refs` does its session-keyed copy-on-prune step when channel retention triggers (separate, retention-driven, not at RunEnded).

**Phase 3 — At rest (post-run).** Two query surfaces, two purposes:
- **Parquet** is the analytical surface — `SELECT … FROM measurements WHERE …` by name / value / outcome. `out_*` columns hold scalar snapshots or claim URIs. Joins to part / station / spec for SPC.
- **Event log** stays queryable for replay / audit / "what exactly happened in this vector." Filter by `event_type`, `run_id`, `session_id`, `event_number`.
- **Following a URI** is the raw-data drill-down — `file://…` resolves via FileStore (artifact viewer renders or serves bytes); `channel://…` resolves via ChannelStore (rows for the session, plot or compute).

### Time sync — point measurements ↔ continuous artifacts

A **measurement** is a point event — one `received_at` timestamp. A **vector / step / run** is a range — bracketed by `*Started`/`*Ended` events. A **continuous artifact** (video, audio, long DAQ file) is also a range — bracketed by `StreamStarted`/`StreamEnded`.

Sync mechanic — same shared event clock; subtraction:

| You want | Compute |
|---|---|
| Video moment for a single measurement | `measurement.received_at − StreamStarted.received_at` → one offset (seconds) |
| Video segment for a vector | `(VectorStarted − StreamStarted, VectorEnded − StreamStarted)` |
| Channel samples concurrent with a measurement | `ChannelStore.query(channel_id, since=measurement.received_at − ε, until=measurement.received_at + ε)` |
| Channel samples covering a vector | same query, keyed on vector's start/end |

The viewer seeks to a point (`#t=12.3` per W3C Media Fragment URI) for a measurement, or plays a range (`#t=12.3,18.7`) for a vector / step / run. Both honored by HTML5 `<video>` natively.

**One clock for everything.** All events ride the same `received_at`; offset arithmetic does the sync. No per-artifact sync protocol, no synchronized-clock machinery beyond "all events written to the same log."

### Why events aren't the *search* surface

The event IPC schema (`event_log.py:31-40`) is typed envelope columns (`event_number` / `event_type` / `session_id` / `received_at`) + a single **opaque `json` string** holding the payload. Filtering by envelope is fast (typed columns); filtering by anything inside the JSON (min, max, units, channel_id, limit) is full scan + per-row JSON parse + no statistics + no pruning. This is why per-store attribute indexes (long-term build item) exist as a separate surface — events are the spine, but not the searchable index.

---

## 9. Consumer SDK — `litmus.live`

Read-side counterpart to the verbs. Provides one ergonomic entry point for UIs, MCP tools, custom dashboards, and external integrations. **Hides** transport selection (Flight vs HTTP range) and URI parsing; **doesn't hide** the underlying data shape (samples vs bytes vs structured events — those are real and the consumer's render layer dispatches on them).

### Three subscription primitives + one resolver

```python
from litmus.live import LiveClient, EventFilter

client = LiveClient.connect(url="http://localhost:8000")

# 1. Event subscription — the timeline routing layer
async for ev in client.subscribe_events(EventFilter(run_id="abc123")):
    if isinstance(ev, MeasurementEvent):
        table.add(ev.name, ev.value, ev.outcome)
    elif isinstance(ev, ObservationEvent):
        if ev.claim and ev.claim.scheme == "file":
            viewer.render(ev.claim.deref())
        elif ev.claim and ev.claim.scheme == "channel":
            spawn(subscribe_to(ev.claim.channel_id))

# 2. Channel subscription — per-sample push (typed numeric batches)
async for batch in client.subscribe_channel("scope.ch1"):
    plot.append(batch.samples)

# 3. File subscription — partial reads with frame-index notifications
async for chunk in client.subscribe_file("file://_ref/dut_video.mp4", live=True):
    video_decoder.feed(chunk.bytes)

# 4. Convenience: subscribe to a whole run's live updates
async for update in client.subscribe_run_live("abc123"):
    update.dispatch(
        on_measurement=lambda m: table.add(m),
        on_channel_batch=lambda b: plots[b.channel_id].append(b.samples),
        on_file_chunk=lambda c: viewers[c.file_uri].update(c.bytes),
    )

# 5. Resolver — one-shot fetch any claim URI
data = client.deref("channel://scope.ch1?session=…", window=(-30, 0))   # last 30s
img = client.deref("file://_ref/front.png")
```

### Where it fits

| Layer | API | Audience |
|---|---|---|
| Stores | `ChannelStore.write/query`, `FileStore.put/range_read`, `EventLog.emit` | platform-internal |
| Verbs (writes) | `observe` / `verify` / `stream`, `observer.read`, `channels.*`, `filestore.*` | producers (test authors, drivers) |
| **Consumer SDK (reads)** | `litmus.live.LiveClient` | **consumers (UIs, MCP tools, custom dashboards, external integrations)** |
| Transport | Arrow Flight, HTTP + range, IPC | platform-internal |

---

## 10. Live → archival lifecycle

Channels and Files aren't parallel competitors — they're **two lifetime phases of the same continuous data**:

| Phase | Store | Optimized for | Lifetime |
|---|---|---|---|
| **Live working memory** | ChannelStore | per-sample subscribe, per-sample query, high-rate writes, watching during the test | days to weeks (retention-windowed) |
| **Durable archive** | FileStore | self-contained run, portable, render/download, long-term retention | indefinite |
| **Bridge** | `materialize_channel_refs` (copy-on-prune) | promote run-referenced channel data to file form before pruning | retention-triggered |

So a Waveform written via `observe("scope.ch1.capture", wf)`:
1. Lands as a ChannelStore row immediately → live + subscribable + per-sample queryable.
2. `out_scope.ch1.capture = channel://…?session=…` stamped on the vector at materialization.
3. Eventually (retention prune) → channel data copies into `runs/_ref/` as `.npz` (Waveform round-tripped via `np.savez` or similar).
4. Parquet URI rewrites `channel://` → `file://…/scope.ch1.capture.npz`.
5. Same waveform, archival form, run still self-contained.

The author never sees the lifecycle — they call `observe(name, wf)` and the system handles it.

**Materialization is session-keyed and copy-on-prune** (not at RunEnded). `materialize_channel_refs` runs *before channel pruning*: collects `(channel_id, session_short)` pairs, asks the runs DuckDB index `find_channel_refs(session_shorts)` which parquet rows reference them, reads each via `store.query(...)`, writes a `file://` sidecar into the run's `_ref`, rewrites `channel://` → `file://`. At RunEnded the parquet still points at the **live** `channel://`; only *blob observations* go to `_ref` at RunEnded (via `save_test_run`). Two distinct `_ref`-write moments.

Nuance: channel data is **session-granular, not run-granular** (rows carry `session_id`, no `run_id`). A run materializes the whole session-channel it referenced; two runs sharing a session + channel each get a copy. Fine for one-run-per-session; duplicative otherwise.

---

## 11. Current gaps in source (the build items address these)

1. **Images/blobs are dropped, not stored.** `InstrumentRead._serialize_with_claim_check` (`events.py:543`) claim-checks arrays to a channel but for blobs falls through to `data["value"] = repr(v)` — even though `EventLog.save_ref` is right there. The "finally picks up our images" fix.
2. **No streaming sink.** `save_ref_to_dir` writes whole values. `StreamStarted` / `StreamEnded` / `StreamFrameIndex` events at `events.py:616` are defined but no writer backs them.
3. **No live home for produced files.** `_ref` at the RunStore is materialization-only; the EventLog `_ref` is live but unused for blobs. Produced files during a run are held in-memory — won't survive a crash, can't hold a video.
4. **`observe()` emits no event.** `Context.observe()` (`harness.py:190`) writes arrays to the channel but emits no event — manually-observed captures are untraceable, invisible to live subscribers, missing from the timeline.
5. **`observer.read` doesn't stamp the vector's `out_*`.** Scalar instrument readings link to verify rows only via the event log today, not via row columns. Breaks the polymorphic `observe`/`verify` symmetry. (Note: under Position 2 — see §8 — per-sample events are retired; stamping happens once per (vector, channel) pair on first write into that vector, not per sample.)
6. **`observe`/`verify` route incorrectly for arrays today.** `classify_value` (`ref.py:19-39`) sends `numeric_array` → ChannelStore for any caller, which is right for stream-from-instrument but currently bypasses the explicit-verb model. Need the dispatch to honor the verb's intent.
7. **Metadata/attribute search is not performant.** Events are opaque JSON for filter-on-payload; the only typed index (run parquet) is run-scoped, missing standalone data. (Under Position 2 the volume is dramatically smaller — only lifecycle events stay in EventStore — but the typed-payload work (item 21) still helps for the lifecycle events that DO exist.)
8. **ChannelStore type support is limited / lossy.** `_infer_field_type` (`channels/models.py:45-66`) casts scalar `int` → `pa.float64()` (truncates large ints; loses int semantics); `_infer_schema` array branch (line 86) **hardcodes** `pa.list_(pa.float64())` regardless of element type. Result: a list of `bool` (digital waveform) round-trips as `[1.0, 0.0, 1.0]`; a list of `str` (status stream) is broken; numpy dtypes are erased. Only float arrays work cleanly.

---

## 12. Build items

> **Scope commitment (per the v0.2.0 decision):** v0.2.0 is the release where data architecture stabilizes for pre-1.0. We're fixing *all* the data stuff from 0.1.x. That means the originally-long-term items L2-L7b move into MVP scope. Only L1 (per-store attribute indexes — genuinely greenfield index work) stays long-term. Calendar is multi-month; see §16 for milestones.

### Status (v0.2.0 stage branch `feat/0.2.0-data-improvements`)

| Item | Title | Cluster | Status | PR |
|---|---|---|---|---|
| 1a | FileStore put API + URI scheme | C1a | ✅ DONE | #14 |
| 1b | FileStore lifecycle Stream events (StreamStarted / StreamEnded — per-chunk FrameIndex DROPPED per Position 2 parity) | C5 | ✅ DONE | (this PR) |
| 1c | FileStore attributes + MIME typing | C4-partial | ✅ DONE | #23 |
| 1d | Unify the two `_ref` dirs | C4-mid | ✅ DONE | #25 |
| 1e | FileStore integration test | C4-remainder | ✅ DONE | (this PR) |
| 2 | Streaming sink (raw / jsonl / tdms / h5; PyAV + soundfile → item 23 follow-up) | C5 | ✅ DONE | (this PR) |
| 3a | `Context.observe` blob → `file://` claim | 3a | ✅ DONE | #15 |
| 3b | `observer.read` blob → `file://` + ChannelStore `scalar:str` | C-3b | ✅ DONE | #19 |
| 4 | `observe()` emits Observation event | 4 | ✅ DONE | #16 |
| 4b | `ChannelStarted`/`ChannelClosed`; retire `InstrumentRead` | C1 | ✅ DONE | #17 |
| 5 | `observer.read` stamps vector `out_*` (rename DEFERRED) | C1 | ✅ DONE | #17 |
| 6 | Verb dispatch by value shape — `observe` is the polymorphic router (all shapes); `verify` is scalar-only and raises on non-scalar | C3a | ✅ DONE | (this PR) |
| 7 | `stream(name, sample)` test-author verb (Context.stream + bare fixture) | C3b | ✅ DONE | (this PR) |
| 8 | Symmetric `channels.{write,stream}` / `files.{write,stream}` | C3b + C5 | ✅ DONE | (C3b shipped stub; C5 closed it) |
| 9 | Auto-promotion rule in materializer (≥1 verify → verify rows; 0 + ≥1 observe → DONE row) | C6-partial | ✅ DONE | #21 |
| 10 | Type-stable `out_<name>` registry | C6-partial | ✅ DONE | #21 |
| 11 | Schema rename `timestamp` → `received_at` + nullable `sampled_at` | C7 | ✅ DONE | #20 |
| 11b | Schema rename `samples` → `value` (unify payload column across scalar/array shapes) | C3a-pre | ✅ DONE | (this PR) |
| 12 | Promote `save_ref_to_dir` to registry | C6-remainder | ✅ DONE | #22 |
| 13 | MIME + extension + attributes on artifact metadata | C6-remainder | ✅ DONE | (this PR) |
| 14 | Typed leaf-type support (scalar/array × bool/int/float/str) | C2 | ✅ DONE | #18 |
| 15 | `XYData` model + complex-array round-trip coverage | C8 | ✅ DONE | (this PR) |
| 16 | Optional `namespace=` kwarg on observe/verify/stream — symmetric across all five power-user surfaces (Context.observe/verify/stream + channels.{write,stream} + files.{write,stream}) | C3a + cluster-4 | ✅ DONE | (cluster-4 phase-5 closed files.{write,stream} parity) |
| 17 | Rename metadata fields → `attributes` across schemas | C2 | ✅ DONE | #18 |
| 18 | Live waveform plot on channels detail page | C10 | ✅ DONE | #34 |
| 18b | `/channels/{id}` chart: render-time session grouping with operator-readable legend (`<dut_serial> · <YYYY-MM-DD HH:MM:SS>`) — scalar channels with samples from 2+ sessions render one series per session, distinct color per session, legend on. Single-session views unchanged. | C10 follow-on | ✅ DONE | (this PR, task #196) |
| 19 | Payload-filter perf baselines (item 21 baseline) | C11 | ✅ DONE | #37 |
| 20 | Consumer SDK (`litmus.live`) | C10 | ⏳ PENDING | — |
| 21 | Typed Arrow event payloads (22 ids/names promoted to typed DuckDB columns; outcome filter 2.74×, role filter 3.7× via projection narrowing) | C11 | ✅ DONE | #39 |
| 22 | Local shared-memory transport (DEFERRED 2026-06-03 — PoC at `.tmp/shm_perf_poc/` showed 2× per-batch latency ceiling, not the 3–10× / GB/s estimated in §2/§3. Flight on loopback is faster than the original estimate (175 µs p50, 284 MB/s) and the remaining 40+ µs/batch on shm is dominated by Arrow IPC serialize/deserialize. Cost/benefit doesn't justify the multi-week Transport abstraction + lifetime/crash engineering for a 2× win when no user has reported lag. Revisit only on symptoms: UI lag pinned to transport, sustained >10 kHz capture saturating Flight, or many-subscriber fan-out scaling.) | C11 | ⏸️ DEFERRED | — |
| 23 | Hardware video encoder option (also lands PyAV `mp4` + soundfile `wav` formats) | C5 follow-up | ⏳ PENDING | — |
| 24 | (TBD per design doc growth) | C5 | ⏳ PENDING | — |

**Parked deliberations (not blocking any cluster):**

- **Channel-id source/disambiguation field** — see §5 *Naming conventions and uniqueness* and the C3 introduction. User-sourced channels (test-author `observe`/`stream` calls) can silently merge when two tests use the same name for different concepts; C2's type-stability catches the type-mismatch case loudly but same-type-different-meaning is residual. **Status 2026-06-05 — operator-visible half resolved.** The chart-side concern ("operator looks at /channels/{name} and can't tell which run a sample came from") is closed by item 18b's render-time session grouping: each session gets its own series + legend entry. The producer-side concern (two tests in the same project writing to the same name for different concepts) remains addressed by item 16's `namespace=` + the §5 naming discipline. The originally-deferred `step_path` / `vector_id` columns on the ChannelStore row schema are no longer load-bearing — the operator distinguishes by session in the chart, and analytics can already join channels to runs via the session-bearing claim URI. **Closes task #195** without a schema change.
- **`EventEmitter.read` rename** — agreed `read` is a sample-arrival hook on EventEmitter that may emit, not a 1:1 verb. Original design-doc rename (`read` → `record_read`) was verb-shaped at a layer that should stay machinery. Deferred to a later cluster; pick a non-verb name then.
- **`FileStore._resolve_uri` walks date dirs** — landed in item 1c (PR #23). Today's URI is logical (no date), so resolution walks `{files_dir}/*/{session_id}/{filename}`. Cost: O(days) per resolution in the worst case (cross-session/historical reads); 1 stat in the dominant case (within-session reads hit today's date dir). Acceptable for now because within-session is the hot path. Long-term fix is **L1 (per-store attribute indexes)** — already on the long-term roadmap as "genuinely greenfield index work"; L1 turns `read_attributes` and `_resolve_uri` into indexed lookups (DuckDB / parquet manifest, peer to the runs daemon). If the walk bites before L1: (a) in-process `{uri: Path}` resolution cache for repeated reads (cheap, ~20 lines); (b) per-session-dir index sidecar (tiny per-session write for O(1) lookup). Don't pre-optimize; revisit when a real workload measures the bottleneck.
- **Materialize-on-prune output format** — `materialize_channel_refs` writes channel data as `.arrow` IPC stream. Works fine for DuckDB / pyarrow / pandas; less idiomatic for R / Spark / BI tools where `.parquet` is the archive format. Item 14's typed leaf-types make per-shape format dispatch tractable: `.parquet` for scalar/struct/str channels, `.npz` for array channels with `sample_interval` (matches Waveform's existing serialization). Self-contained future cluster — extends the C6-remainder registry rather than changing item 1d. Not blocking the bundle's portability today (everything in FileStore is reachable; the format question is about which tool can crack a single file open).
- **Cross-store retention coordination** — item 1d moves materialized artifacts from `runs/{stem}_ref/` (sibling to the parquet) to `files/{date}/{session_id}/` (separate tree). Today's retention only walks `runs/`. If a user prunes parquets, the FileStore artifacts they referenced get orphaned (dangling URIs); if they prune FileStore but keep parquets, parquet URIs break. Recommendation when retention gets real: walk both trees, intersect with surviving references, prune the diff. Closely related to L1 (which would make the reference-intersection an indexed query). Note in retention design when it next gets touched.
- **Session-first layout reorg** — today's `data_dir/{runs,events,channels,files}/` is type-first; could become session-first `data_dir/sessions/{date}/{session_id}/{run.parquet,events/,channels/,files/}` for cleaner per-session retention + operator mental model. Net change: glob-pattern updates in 4 discovery loops (`runs/*/*.parquet` → `sessions/*/*/run.parquet`, etc.). Channel cross-session sharing constraint: `channels/{date}/{ch}_{sid}.arrow` files are intentionally per-channel-then-session so a long-running fixture channel accumulates across runs; session-first per-channel buckets would break that. Either keep channels separate, or design per-session channel writes + a cross-session view. Significant arch refactor — defer to v0.3.0 or late v0.2.0 cleanup. Discussed in 1d planning and explicitly deferred.
- **Streaming as a capability both stores can offer** — surfaced in C4-remainder while scoping item 1b. ChannelStore is already streaming-shaped (append-only, subscribable, Flight `do_get`). FileStore becomes streaming-capable with item 2's sink (`open(key, format) -> sink; sink.write(chunk); sink.close() -> URI`). What gets streamed differs (typed samples vs byte chunks of one artifact), but the lifecycle pattern is symmetric. This shows up in two design questions: (a) the verb surface in C3 — `stream(name, sample)` should be uniform across both stores (item 7 + item 8 `channels.stream` / `filestore.stream`); (b) the lifecycle event types — today `ChannelStarted`/`ChannelClosed` (Position 2, channels) and `StreamStarted`/`StreamFrameIndex`/`StreamEnded` (for FileStore streams) do the same job for two stream types. Could collapse into one Stream* family with a `source` discriminator, or stay separate because the keying differs (channel_id stable across sessions vs stream_id per-instance UUID). Decide when C3 / C5 actually shape the verbs and the sink. Don't unify the events preemptively — that's a downstream consequence of the verb design, not an item 1b/2 decision.
- **Channels + files as test INPUTS (v0.3.0 follow-on — punted 2026-06-06)** — surfaced 2026-06-03 reviewing the v0.2.0 remaining-work scope; briefly pulled into v0.2.0 on 2026-06-05 then **punted back to v0.3.0 on 2026-06-06** after a deeper spitball revealed an alternative shape worth designing properly. The interesting question isn't "ship `channels.read` / `files.read` symmetric to write" — it's whether `in_*` columns can hold URIs the way `out_*` does, with `context.get_input(name)` polymorphically unpacking on access (scalar inline → return literal; URI → resolve and return unpacked value). That makes traceability inherent (the `in_*` column IS the trace, no `InputLinked` event needed) and makes test bodies symmetric on both sides. But it surfaces real questions about eager-vs-lazy timing, URI lookup ergonomics, and lifecycle/retention semantics that need user-facing patterns settled before code. **Decision**: ship v0.2.0 without inputs to avoid half-designing the polymorphic-input shape under release pressure. Re-open in v0.3.0 with both layers in scope: (a) imperative read verbs (`channels.read`, `files.read`) as plumbing; (b) `context.get_input` unpack-on-access as the test-author surface. See 2026-06-06 chat transcript for the spitball + tradeoff matrix. Today's verb surface (`observe`/`verify`/`stream`) is producer-only; ChannelStore and FileStore have read APIs (`store.query(...)`, `store.read(uri)`) but no test-author-facing way to consume prior data as test stimulus. Real T&M workflows pull historical data forward: reference-waveform comparison ("compare this DUT's response to a golden-unit waveform captured last week"), replay ("feed this recorded signal as stimulus"), calibration carry-over ("read the cal file attached to the last successful run on this fixture"), cross-session correlation ("compare to the same measurement across the last 10 runs of this part"). Without a first-class input surface, test authors hand-roll `ChannelStore(...).query(...)` (breaks layering, no traceability) or ship reference data in the repo (no audit trail). Design questions to settle when this is picked up: (a) surface shape — bare verbs (`load_channel(...)`) or attached namespaces (`channels.read(...)` / `files.read(...)` — likely the latter, symmetric with `channels.write` / `files.write` from item 8); (b) traceability — does loading emit a typed event (`InputLinked`?) so the run records its dependencies?; (c) retention contract — loud `MissingInputError` vs return `None` vs replay-from-archive when the referenced channel was pruned (ties to cross-store retention deliberation above); (d) lookup ergonomics — by URI, by `(session_id, channel_id)`, or by higher-level query ("the latest `measurement_name='vout_idle'` on `dut_serial='SN001'`"); the typed-column promotion from PR #39 makes that query cheap; (e) pin-aware loading for part-spec workflows ("the reference for this pin's vout characteristic" instead of channel_id). Out of scope for the initial design: comparison verbs on top of loads (that's `verify()` territory operating on loaded data), schema migration of prior-run data with old column layouts (loud-fail philosophy applies), multi-session aggregation (analysis/SPC territory). Defer to v0.3.0 — verb surface and store APIs need to settle in v0.2.0 first.

### MVP (initial release) — stores + API consistency + types

**Stores:**

Item 1 is the foundation; decomposed for execution clarity.

1a. **FileStore put API + URI scheme.** Implement `filestore.put(name, value, attributes) -> file://…` and the URI/path layout. DoD: `tests/test_filestore_put.py` — put a Path / bytes / Waveform / Pydantic model; assert returned URI resolves to a file on disk; metadata roundtrips.
1b. **FileStore live lifecycle + Stream events.** Wire `StreamStarted` / `StreamFrameIndex` / `StreamEnded` events around the put + streaming-sink paths. DoD: events emit in correct order with correct payloads; subscribers see them via the existing event subscription path.
1c. **FileStore attributes + MIME typing.** Capture attributes at put time (mime, extension, size, dimensions/duration where format library exposes them). DoD: `tests/test_filestore_attributes.py` covers each built-in format's metadata extraction.
1d. **Unify the two `_ref` dirs.** Migrate `events/{session_id}_ref/` + `runs/{stem}_ref/` into one canonical FileStore layout. DoD: existing code paths that read from either location continue working (or are updated); single source of truth on disk.
1e. **FileStore integration test.** End-to-end test that observe → file:// claim in event → materialized into parquet `out_*` column → range-served via artifact viewer. DoD: golden-path test passes; failure modes (missing file, malformed URI) raise typed errors.

2. **Streaming sink** behind the existing `Stream*` events (`events.py:616-628`) — `open(key, format) -> sink; write(chunk); close()`. Wraps PyAV / soundfile / tifffile / nptdms / h5py / pyarrow per format. Final `file://` claim in `StreamEnded`. DoD: write video / audio / TDMS via the sink; subscribe to `StreamFrameIndex` events; range-read partial bytes during write.

**API consistency (fixes gaps 1, 3, 4, 5):**

3a. **`Context.observe` blob → `file://` claim-check** — fixes half of the image-drop. Today `Context.observe` (`harness.py:200-207`) stashes raw blobs in `self._observations[key] = value`; under 3a it routes through `FileStore.put` and stashes the URI instead. Lands clean today — no event coupling, no dependency on the channel-event reshape. DoD: `tests/test_observe_blob_claim.py` writes a Path / bytes / Pydantic value via `observe()`, asserts `_observations[key]` is a `file://` URI and the bytes resolve.

3b. **`observer.read` blob → `file://` claim-check + ChannelStore str-typed write** — fixes the other half of the image-drop. The proper place for instrument-blob handling under Position 2 is `observer._store_value` (`observer.py:63-72`), not the InstrumentRead serializer (which retires under item 4b). Routes blob to FileStore, writes the URI back to ChannelStore as a `scalar:str` channel value (uses item 14 typed leaf-types). **Depends on item 14** (typed leaf-types — ChannelStore must accept str values cleanly) **and item 4b** (new channel-lifecycle event story). DoD: `tests/test_instrument_blob_claim.py` writes an instrument-sourced blob, asserts the URI lands in ChannelStore as a `scalar:str` channel + bytes resolve from the URI.

(Sequenced: 3a now — independent. 3b after 4b + 14 land.)
4. **`observe()` emits a claim event** the way `observer.read` does. Adds `Observation` event for every observe call (scalar inline; non-scalar carrying URI). DoD: every `observe()` call results in exactly one event in the EventStore; consumers can subscribe and react.
4b. **Introduce `ChannelStarted` + `ChannelClosed` event classes; retire per-sample `InstrumentRead`.** (Position 2; see §8.) Two new event classes in `events.py`:
    - `ChannelStarted(channel_id, data_type, units?, instrument_role?, method?, resource?)` — fires once per `(channel_id, session_id)` on first write into a channel. Carries instrument fields when source is an instrument; null otherwise.
    - `ChannelClosed(channel_id, reason)` — fires when a channel is sealed for a session (session_ended) or retention-pruned. Useful for consumers tracking "still being written to" vs "no more data coming".
    - `InstrumentRead` itself is **retired** in v0.2.0 — per-sample events go away. Instrument identity moves to `ChannelStarted`'s optional fields + the existing `InstrumentConnected` (already fires once at connect time; carries `manufacturer`/`model`/`serial`/`firmware`/`cal_*`).
    - Per no-backcompat principle: no alias for `InstrumentRead`; consumers reading the old event type get migrated to read `ChannelStarted` / subscribe to ChannelStore directly for sample data.
    - DoD: `tests/test_events_channel_lifecycle.py` covers `ChannelStarted` firing once per (channel, session), `ChannelClosed` firing on close, instrument-source vs non-instrument-source field population, and no per-sample event during a multi-sample channel write.
5. **`observer.read` stamps the vector's `out_*`** so scalar instrument readings link to verify rows via row columns, not just events. **Position 2 reshape:** per-sample events are retired (see §8 + new item 4b); stamping happens once per `(vector, channel_id)` pair on first write into that vector, not per sample event. `observer.read` itself: writes channel + emits `ChannelStarted` on first write per channel/session + stamps vector `out_*` on first write per vector. **This PR also renames `observer.read` → `record_read`** (the naming-smell rename rides this change since the file is touched). DoD: verify rows in a vector that had `observer.read` calls show `out_<channel_name>` columns; ChannelStarted event fires exactly once per (channel, session); rename grep returns zero old references.

**Dispatch (fixes gap 6):**

6. **`observe` dispatch by value shape; `verify` is scalar-only.** Channel-shaped numerics (Waveform, numeric ndarray) → ChannelStore; arbitrary bytes/formats → FileStore. References (handle, URI, Path-already-in-FileStore) stamped without re-write. `verify` is judgment-bearing: pass a non-scalar and it raises `TypeError` pointing at `observe`.
7. **`stream(name, sample)` verb** as the test-author-facing channel-write sugar (sugar over `channels.write`). One-line append-a-sample; never auto-associates. **Position 2:** emits `ChannelStarted` once per channel/session on first write; subsequent writes go to ChannelStore only (no per-sample event). DoD: `tests/test_stream.py` covers per-sample channel write + `ChannelStarted` fires exactly once + ChannelStore receives every sample + no per-sample event in the EventStore.
8. **Symmetric streaming verbs**: `channels.write` / `channels.stream` + `filestore.put` / `filestore.stream`. Both one-shot and context-managed shapes per store. **Position 2:** `channels.write` / `channels.stream` follow the same lifecycle-event-only pattern as item 7.

**Materialization:**

9. **Auto-promotion rule in the materializer.** Per vector: `≥1 verify` → verify rows only; `0 verify, ≥1 observe` → each observation promotes to a DONE row.
10. **Type-stable `out_<name>` registry.** First observation of a name registers the column kind; subsequent observations must match. Mismatches error at materialization.

**Schemas:**

11. **Rename ChannelStore row `timestamp` → `received_at`; add nullable `acquired_at`.** Same-shape rename plus one new nullable column on both `SCALAR_SCHEMA` and `ARRAY_SCHEMA`. Pre-1.0, mechanical.

**Serialization:**

12. **Promote `save_ref_to_dir` to a registry.** Built-in handlers for existing types (`Path`, `Waveform`, `bytes`, `BaseModel`, `ndarray`, fallback `pickle`) + opportunistic `PIL.Image` → PNG, `pandas.DataFrame` → Parquet. Expose `filestore.register_serializer(type, fn)` and a `litmus_serialize(dest_dir, stem) -> Path` protocol for objects that know their own format. Pickle fallback emits `RuntimeWarning` naming the type.

**FileStore typing:**

13. **MIME + extension + attributes** on artifact metadata. Litmus convention table for vendor formats (NPZ, NPY, TDMS, pickle).

**ChannelStore typed leaf-types:**

14. **Typed leaf-type support across scalars and arrays in ChannelStore.** Closes gap 8.
    - Scalar `int` preserved as `pa.int64()` (not cast to `float64`). Fixes truncation hazard for large ints.
    - Array element type inferred from `value[0]` (for lists/tuples) or numpy `dtype` (for ndarrays), instead of hardcoded `pa.list_(pa.float64())`. Supports `list<bool>` (digital waveforms), `list<int>`, `list<str>`, plus the existing `list<float64>`.
    - `ChannelDescriptor.data_type` extended to carry the leaf type (e.g., `"scalar:bool"`, `"array:bool"`, `"scalar:int"`, `"array:str"`) so subsequent writes validate against the kind-registry pattern.
    - Legacy `SCALAR_SCHEMA` / `ARRAY_SCHEMA` (float-only) stay as fallbacks for empty-query results.
    - Flight `encode_value` already utf8 + JSON-encoded — works for any type today; typed Flight transport is a future perf optimization (post-v0.2.0; not committed).
    - Small lift; concentrated in `models.py:45-95`.

**Related/composite data helper:**

15. **`XYData` model + complex-array verification.** Small Pydantic model for paired arrays (`x`, `y`, optional `x_units`/`y_units`/`x_name`/`y_name`); registered with the serialization registry (item 12) so `observe(name, XYData(...))` routes to FileStore as `.npz`. Plus a test that verifies `numpy.complex128` / `complex64` arrays round-trip cleanly through the registry (they should — numpy primitive dtype + `np.save` handles them natively; just needs explicit coverage). Trivial lift; formalizes the "Pattern B" workflow from §4.

**Naming convenience:**

16. **Optional `namespace` argument on `observe` / `verify` / `stream`.** Addresses the channel-collision risk for test authors (§5 naming subsection). Author can write `observe("voltage", v, namespace="psu_under_test")` instead of `observe("psu_under_test.voltage", v)`. The effective `channel_id` is the dotted form. Drivers already get this for free via `observer.read` (instrument_role auto-prepends); this exposes the same convenience to test code. Small lift on the verb signatures; non-breaking (omitting namespace = today's behavior). Pre-1.0.

**Schema vocabulary consistency:**

17. **Rename metadata-dict fields to `attributes` across all schemas.** Today three different names for the same concept:
    - `ChannelDescriptor.properties` (`channels/models.py:27`)
    - `Waveform.attrs` (`data/models.py:485-515`)
    - future `FileArtifactMetadata.attributes` (build item 1)

    Pick **`attributes`** as canonical: matches HDF5 / xarray / pandas (newer Python data-science ecosystem); avoids `@property` decorator ambiguity; user-facing in `Waveform` (test authors construct `Waveform(t0, dt, Y, attributes=...)`). Mechanical rename in three places + their call sites. Pre-1.0; no backcompat shim. Sets up clean cross-store attribute queries when L1 lands (same field name on every store). Small lift; the design doc already refers to "attributes" everywhere — this is the source-side alignment.

**Live + perf:**

18. **Live waveform plot.** Subscribe the channel detail page (`ui/pages/channels/detail.py`) to the event stream; redraw on each new `InstrumentRead`. The list page already live-updates via event subscriptions; copy that pattern. DoD: detail page redraws per new sample without manual refresh; visible during a long acquisition.

19. **Perf: byte-aware flush + end-to-end Flight bench.** `BufferedIPCWriter` flushes by **row count** (fine for scalars; dangerous for dense arrays). Switch to byte-aware threshold + add an end-to-end Flight streaming bench (the existing `test_data/test_perf.py` measures local writes only). DoD: bench validates the §2 performance estimates within order-of-magnitude; byte-aware flush stable under load.

20. **Consumer SDK (`litmus.live`)** — typed event objects, `subscribe_events` / `subscribe_channel` / `subscribe_file` / `subscribe_run_live` / `deref`. **Commit to async** as the canonical API (matches Flight `do_get`'s natural generator semantics + non-blocking UI consumers). Hides transport + URI dispatch; doesn't hide data shape. DoD: `tests/test_litmus_live.py` covers each subscription primitive + `deref`; a sample MCP tool consumes runs via the SDK.

21. **Typed Arrow event payloads.** Replace the opaque `json` string payload column in `event_log.py:31-40` with native nested Arrow structs per event type. Two reasonable schema shapes — union payload column or per-event-type record batches in one IPC file (the latter closer to how Arrow IPC already works). Either way, the envelope (`event_number`, `event_type`, `session_id`, `received_at`) stays typed and indexed; payload becomes typed too.
    - **Write gain:** 5–10× per event (no per-event JSON encode). Under Position 2 (see §8 + item 4b) event volume drops dramatically because per-sample channel events are retired, so the aggregate write-throughput pressure on EventStore is much lower than pre-Position-2 estimates suggested.
    - **Read gain on payload filters:** **10–50×** (columnar pushdown + DuckDB statistics vs full-scan + JSON parse). Still the bigger number for the lifecycle events that DO exist (filtering `ChannelStarted` by `instrument_role`, `InstrumentConnected` by `model`, etc.).
    - **Closes the "events aren't a search surface" gap** for any predicate inside the payload.
    - Substantial lift — every event consumer (UI, materializer, MCP tools, exporters) reads from JSON today; all need updating. Plan for proportional execution time.
    - **Urgency drops under Position 2** — without the per-sample event flood, the JSON payload bottleneck is no longer the dominant perf issue. Still worth doing for correctness + queryability of lifecycle event payloads, but lower-priority than pre-Position-2.
    - DoD: payload-field filter queries (e.g., `event_type = 'ChannelStarted' AND payload.instrument_role = 'scope'`) execute against typed columns; existing consumers continue working.

22. **Local shared-memory transport for Consumer SDK.** When subscriber is on the same machine as producer, use `multiprocessing.shared_memory` (or POSIX `shm`) for zero-copy reads instead of Flight over loopback. Transport selection auto-detects local vs network.
    - Symmetric gain on reads and writes (3–10× for local subscribers); consumer CPU drops to near-zero.
    - Combined with item 21, lifts EventStore + ChannelStore live throughput into the **GB/s range** for local deployments.
    - **Substantial lift** — custom shm-backed Arrow IPC needs careful lifetime management (when does the shm get reaped? what happens on consumer crash?). Closer to a focused mini-project than medium.
    - DoD: local subscribers route via shm automatically; remote subscribers stay on Flight; perf bench shows the gap.

23. **Hardware video encoder option.** `filestore.stream(name, format="mp4", hwaccel="auto")` flips PyAV from software H.264 to NVENC (NVIDIA) / VAAPI (Intel) / VideoToolbox (Mac). Estimated 10–20× video write throughput gain. DoD: writing video via the sink uses hardware encoder when available; falls back to software cleanly when not; documented in artifact viewer + streaming-sink docs.

24. **Hardware video decoder option** for FileStore playback consumers — companion to item 23 on the read side. Today software H.264 decode in the UI consumer caps video playback at ~50–200 MB/s (CPU-bound). Hardware decode (NVDEC, VideoToolbox decode, VAAPI) lifts decode to 500 MB/s – 2 GB/s. Same library (PyAV) exposes hwaccel for decode; expose on the Consumer SDK file-watch API. DoD: video playback uses hardware decode when available; fallback works.

**Documentation work (ships with v0.2.0):**

25. **Operator-UI reference page fixes (5 stale pages).** Updates needed for changes that landed in 0.1.3 but missed the docs. Files: `docs/reference/operator-ui/tests.md`, `stations.md`, `parts.md`, `fixtures.md`, `instruments.md`. Tracked in `project_followup_operator_ui_reference_drift.md`. DoD: each page matches running UI; screenshots regenerated.

26. **Operator-UI new reference pages (2 missing).** New files: `docs/reference/operator-ui/duts.md`, `docs/reference/operator-ui/profiles.md`. DoD: each page exists, follows the established operator-ui reference page format, screenshots included.

27. **`docs/concepts/data/three-stores.md` updates to four-store model.** Add FileStore as first-class; update storage layout diagram; reflect the `received_at` / `acquired_at` schema. DoD: page reflects four-store reality; diagrams updated.

28. **New `docs/concepts/data/` pages.** Concept pages for the three-verb model (`observe`/`verify`/`stream`) and for the raw-data layer (FileStore + claim-check). Should cover the user surface as it appears in §3-§4 of this internal note, but pitched at test-engineer audience (no internal class names; no file:line citations). DoD: pages exist; pass the docs-writer agent's audience-fit checks.

29. **`docs/reference/` updates for new verbs.** Anywhere `observe` / `verify` / new `stream` are referenced needs updating. Includes `docs/reference/api.md`, possibly `docs/reference/litmus-markers.md`, `docs/reference/configuration.md`. DoD: reference pages reflect three-verb model; generator pre-commit hook passes.

30. **CHANGELOG `[Unreleased]` entries.** Fill with v0.2.0 entries: data architecture (FileStore lift, three verbs, schema rename, typed leaf-types, MIME typing, serialization registry, auto-promotion rule, Consumer SDK, perf refactors); operator-UI doc drift fixes; new pages; breaking changes (schema rename; `properties` → `attributes`; `observer.read` → `record_read`; `rm -rf data/` migration policy from 0.1.x). DoD: CHANGELOG entries present; entries reference PRs.

**That's the v0.2.0 commitment.** Every captured artifact durably stored; every capture leaves an event with a claim URI; three verbs with consistent dispatch; parquet rows manifest by a single fixed policy; typed leaf-types end-to-end; consumer SDK gives a clean read surface; performance refactors land; supporting docs ship coherent.

### Long-term (post v0.2.0)

L1. **Per-store attribute indexes.** Promote captured attributes (file: width/height/dtype; channel: min/max from `InstrumentRead`) into typed, prunable indexes so questions like *"files where width > 1024"* or *"captures where max > X"* don't require open-every-file or scan-every-event. Channels and files each own their own index; `run_id` is an optional join. The TDMS-properties / DIAdem move. Genuinely greenfield index work — that's why it stays long-term.

---

## 13. Findability tiers

| Tier | What it needs | When |
|---|---|---|
| **From a run / event you already have** ("show me this run's failure capture") | URL resolution of the `file://` / `channel://` claim → existing artifact viewer / ref endpoint | **MVP** |
| **Across runs by analytical outcome** ("yield by station," "failures by Pareto bucket") | Run parquet (already exists) | **today** |
| **Across raw artifacts by attribute** ("files where width > 1024," "captures where max > X") | Per-store attribute indexes (greenfield) | **long-term (L1)** |

---

## 14. Convergence with prior art (post-hoc audit)

**Process note up front — this isn't a derivation map.** Most of the systems listed below were *not consulted during the design*. We worked from "what does T&M data need to do" and arrived at shapes that overlap heavily with what other systems independently landed on for similar reasons. This section is a post-hoc convergence audit.

The one explicit input *was* referenced during design: **ATML / IEEE 1671** — terminology (record_type, measurement, session / run / step / vector hierarchy, traceability columns on every row). Other T&M-native systems (TDMS, OpenHTF, ISO 17025) are convergent precedents, not consulted inputs.

The audit is useful anyway: gives readers from other ecosystems a way to orient, and gives defensible answers when someone asks "why this shape" — because solving the same problem from first principles produces the same shapes other people produce.

### Software-architecture canon (zero novelty — textbook)

| Pattern | Precedent |
|---|---|
| Claim-check (events carry URI, heavy data in stores) | EIP (Hohpe & Woolf, 2003), Azure Architecture Center, Spring Integration |
| Event sourcing (immutable append-only timeline) | Fowler (~2005), Greg Young's CQRS, EventStoreDB, Kafka log-as-database |
| CQRS + materialized views (events = write; parquet = read) | Young (~2010); Kafka + ksqlDB / Materialize; Spark Structured Streaming |
| Three lifecycle phases (live / materialization / at-rest) | CQRS; HTAP databases |
| Star-schema denormalization (vector context onto every row) | Kimball data warehousing |
| Single clock + offset arithmetic for sync | OpenTelemetry trace timestamps; Kafka offsets; SMPTE timecode |
| Schema-on-write / type stability per column | Avro evolution rules; Iceberg/Delta enforcement |
| Type-dispatch serialization registry with fallback | `functools.singledispatch`; `pickle.__reduce__`; MLflow `pyfunc.flavors` |
| Two timestamps per row (storage-time + source-time) | Kafka `LogAppendTime` + creator `Timestamp` |
| Layered API audiences (test author / driver author / platform / consumer) | OTel SDK; pytest hook layers |

### ML experiment-tracking lineage (heaviest convergence, with limits)

| Pattern | MLflow analog | Where the convergence stops |
|---|---|---|
| Backend store + artifact store split | MLflow — exact analog | — |
| Artifact URI from a run/event | MLflow `runs:/<run_id>/path`; W&B Artifacts | — |
| Streaming / chunked artifact upload | MLflow `log_artifact_stream`; S3 multipart | We extend with `Stream*` events for live read-along |
| Run + context + outputs | MLflow `log_param` / `log_metric` / `log_artifact` | We collapse to two intent verbs + a streaming verb |

**Where MLflow stops being the analog.** Two distinct gaps:

*Gap 1 — T&M centrality of raw data.* MLflow treats artifacts as supporting evidence for the structured surface (params + metrics). Browse-and-download; not searchable by content; not part of the analytical query surface. That matches our MVP scope. But MLflow doesn't carry the T&M reality that the raw capture (`.tdms`, scope waveform, camera image) is often *the actual measurement event itself*, and scalars are summaries of it — raw data isn't downstream of measurement, it often *is* the measurement. Two places we go further:
1. Non-scalar observation is a direct verb call — observe(name, wf) hands a Python object; FileStore serializes via the registry. MLflow requires the author to serialize first and `log_artifact(path)`.
2. Auto-promotion to parquet rows — pure-characterization runs (no `verify`, only `observe`) produce DONE rows in our analytical view. MLflow runs with only `log_artifact` calls have an empty metrics surface.

*Gap 2 — relational shape.* MLflow's entities are flat siblings under a run; no row-level relationships. Our analytic shape is opposite: everything denormalized onto the measurement row. Different grain: **MLflow's grain is the run** (few runs, many params/metrics/artifacts; analytics across runs); **Litmus's grain is the measurement row** (many runs, many measurements per run; analytics over measurements). So MLflow gave us the **storage split** (lean metadata + heavy artifact store + claim URI). Star-schema (Kimball) gave us the **analytic shape**. ATML / OpenHTF gave us the **measurement-as-grain** choice.

### Time-series + streaming infrastructure

| Pattern | Precedent |
|---|---|
| Append-only time-series with row-level live delivery | InfluxDB, TimescaleDB, Prometheus, kdb+ |
| Apache Arrow IPC + Flight RPC | Apache Arrow — used by Dremio, InfluxDB IOx, BigQuery, Snowflake, Ray, Dask |
| SCALAR_SCHEMA vs ARRAY_SCHEMA per row | OpenTSDB raw-vs-aggregated; InfluxDB downsampling continuous queries |
| Session/segment file rotation | Kafka log segments; LSM-tree SSTables; log4j rotating appenders |

### T&M-native systems (precedents that matter most to our audience)

| Pattern | Precedent |
|---|---|
| File / group / channel + properties at any level | **TDMS** (NI) — our `Waveform(t0, dt, Y, attrs)` mirrors TDMS waveform |
| Self-describing scientific data files | HDF5 (1988+), NWB, NeXus, Zarr, NetCDF |
| Properties indexed for cross-file search | NI DIAdem (TDMS properties) |
| The four jobs (disposition / traceability / yield / diagnosis) | ISO/IEC 17025, SEMI E10/E58, AIAG MSA, Western Electric / Six Sigma |
| Measurements with limits + outcomes, attachments as side artifacts | **OpenHTF** — `measurements` (with limits) + `phase.attach_from_file()` |
| DUT serial + station + operator + spec on every row | MES / shop-floor data; SEMI SECS/GEM; **IEEE 1671 ATML** (consulted) |
| Session / run / step / vector hierarchy | **ATML** (consulted) |
| Sample-rate-derived time axis (`t0 + dt`) | TDMS waveform encoding; IEEE 1671; MATLAB timeseries |

### Observability lineage

| Pattern | Precedent |
|---|---|
| Vector context propagates to child events | OTel span attributes ↔ our vector `out_*` denormalized onto rows |
| Spans bracket time ranges; events are points | OTel spans + events ↔ our vector/step/run + measurement events |
| `Stream*` events as progress notifications | OTel span events; distributed-trace waterfall markers |

### Web standards (direct adoption)

| Pattern | Precedent |
|---|---|
| `#t=12.3` / `#t=12.3,18.7` media fragments | [W3C Media Fragment URI](https://www.w3.org/TR/media-frags/); HTML5 `<video>` |
| HTTP range requests for partial reads | RFC 7233 |
| fsspec for backend abstraction | pluggable filesystem abstraction |

### Editorial moves (the small, bounded "this is ours")

1. **Auto-promotion rule** (vector with ≥1 `verify` → no DONE rows for observations; vector with 0 `verify` + ≥1 `observe` → each promotes to a DONE row). OpenHTF always promotes; OpenTelemetry never promotes; MLflow never promotes. Our conditional rule serves the use case (characterization-only tests get visible rows; mixed tests don't get noise) — it's the one place a reader from another ecosystem needs the rule explained.
2. **Polymorphic `observe` / `verify` verbs over scalar + non-scalar.** MLflow and OTel chose separate functions per value type. Our choice to fold all value types under one verb is a Python-flavored polymorphism move — better ergonomics, more invisible dispatch. The serialization registry + warning fallback is what makes it safe.
3. **Verb names `observe` / `verify` / `stream`.** "Verify" is T&M-native; "observe" is astronomy / OTel; "stream" is universal. Together they're our trio.
4. **Session ⊃ run ⊃ step ⊃ vector nesting.** Finer-grained than MLflow runs or OpenHTF tests-with-phases. Drove the "channels are session-scoped; runs reference via URI" mechanism.
5. **The "four jobs" framing as a single design rubric.** Each job is industry-canon; the rubric is editorial. Gives the design a defensible spine.

### Net

The biggest risk is *not* novelty — it's that the design looks generic from a distance until you see it specifically matches T&M domain shape (ATML hierarchy, OpenHTF measurement-with-limits, ISO 17025 traceability). The "four jobs" anchor + the T&M-native convergence is what makes it look like a T&M solution rather than an MLflow knock-off — even though we didn't consult MLflow during the design either.

None of the editorial moves are architectural bets. They're surface choices, recoverable. The infrastructure underneath converges with well-trodden patterns and is individually defensible by precedent (post-hoc), even where those precedents weren't the source.

---

## 15. What's still open

Items still genuinely open after the v0.2.0 commitment (most other open items have been promoted to build items 18-30):

- **`acquired_at` source semantics** per driver — what does "instrument time" mean precisely for each instrument? Per-driver decisions during build-out; not blocking the schema change.
- **`Waveform.attributes` landing spot** — descriptor `attributes` vs row-inlined per-acquisition. Defer; not blocking. (Names assume build item 17 has landed.)
- **`channels.stream` context-manager handle shape** — exact methods (`.write`, `.close`, `.flush`, maybe `.flush_bytes(N)`). Bikeshed-friendly, defer to implementation of build item 8.
- **Sample-rate / dt promotion to descriptor** — explicitly **not** doing in v0.2.0; revisit if a real use case lands that needs typed-field queryability (`SELECT channels WHERE sample_rate > 1e6`). Most consumers go through Waveform.dt directly.
- **Per-driver acquisition-time provenance** for `acquired_at` — does the platform interrogate the instrument for its clock, or accept whatever the driver provides? Driver-author choice; document the convention.
- **Station-level environmental daemon** — per-process metadata production today; daemon pattern (one long-running monitor → channels) is a clean future move. Reasonable v0.2.x or v0.3.x add; not blocking v0.2.0.
- **Channel-type-global scoping** — committed for v0.2.0 (global per channel_id); revisit if real adopters hit cross-project / cross-station collisions. Project-scoped or station-scoped registry possible in v0.3.x with adapter for existing data.
- **Backend swap** — architecture is ready (verbs are backend-agnostic); per-backend adapter implementation is v0.3.x+ work, not blocking v0.2.0. See §17.

---

## 16. v0.2.0 release model — fix all the data stuff, then tag

> **Commitment:** v0.2.0 is the release where data architecture stabilizes for pre-1.0. **All** build items in §12 (1a–30) land before the tag fires. Data is important enough that v0.2.0 is gated on completeness, not calendar.

### Execution model — stage branch accumulates, then merges to main

v0.2.0 work accumulates on a **long-lived stage branch** — `feat/0.2.0-data-improvements` (the branch this design doc lives on). Model:

- Each build item (or coherent group) → its own feature branch off `feat/0.2.0-data-improvements`.
- Each branch → small PR → review → merge **into the stage branch** (not into `main`).
- The stage branch accumulates everything v0.2.0 needs.
- **`main` stays at 0.1.x semantics** throughout the v0.2.0 development cycle — clean separation between "what's released" and "what's being built."
- When all MVP build items have landed on the stage branch, the stage branch → one final PR → merge to `main` → tag `v0.2.0`.

| Why this model | Trade-offs |
|---|---|
| Clean "what's in v0.2.0" boundary — one branch diff against main shows the whole release | Long-running stage branch needs periodic rebase from main to absorb any 0.1.x patches |
| Adopters following main get stable 0.1.x throughout development — no half-built v0.2.0 architecture in their installed version | Final merge PR is large (the whole release as one diff) |
| Stage branch can self-test as a whole — integration testing happens continuously as items land | Stage-branch reviewers need to track inbound PRs against the stage, not main |
| Reverts within v0.2.0 are local to the stage branch — no main churn if something needs rework | Rebase / merge conflicts handled in-stage as items land |

### Stage-branch hygiene

- **Periodic rebase from main.** Any 0.1.x patch that lands on main (security fix, critical bug) gets pulled into the stage branch via rebase or merge. Frequency: weekly or when main changes touch a file the stage branch is modifying.
- **PRs into the stage branch use the same review process as main.** Don't lower the bar because "it's just the stage."
- **Tests run on the stage branch** as if it were main — every PR into the stage triggers the same CI gates (lint, type, tests, pre-commit hooks).
- **Stage-branch commits stay tidy.** Squash-merge PRs into the stage so the eventual `feat/0.2.0-data-improvements` → `main` merge has a clean history.
- **No tags on the stage branch.** Tags only fire on main after the final merge.

### Migration policy from 0.1.x

**Pre-1.0; no backcompat shim.** v0.2.0 introduces breaking changes to on-disk Arrow IPC formats (schema rename — `timestamp` → `received_at` + new `acquired_at` column; typed leaf-type expansion; `properties` → `attributes`). Existing 0.1.x data directories will not be readable by v0.2.0.

The supported migration path is:

```bash
# 1. Finish any in-flight runs on 0.1.x
# 2. Export anything you need from existing data (litmus export ...)
# 3. Wipe the data directory
rm -rf data/
# 4. Upgrade to v0.2.0
uv add litmus-test==0.2.0
# 5. Start fresh
```

No `litmus migrate` tool, no read-old-write-new shim, no compatibility flag. Pre-1.0 reality. Documented explicitly in `MIGRATION.md` (build item part of doc work).

### Sharper principle — anything not currently working is not a backcompat constraint

The migration policy above is for the *behaviors that work today*. For everything else: **v0.2.0 doesn't need to preserve broken behavior**. The image-drop bug, the ambiguous `_ref/{filename}` URI scheme that resolves differently depending on whether it's in an events-side or runs-side `_ref` dir, the silent float-cast of `int`/`bool` arrays in ChannelStore, the JSON-payload-only EventStore — these are *bugs being fixed*, not contracts being honored.

This sharpens several build items:
- **Item 1d (unify `_ref` dirs):** the legacy `file://_ref/{filename}` URI shape doesn't need to keep resolving. Old call sites get rewritten to use `FileStore.put` returning the new `file://{session_id}/{filename}` form; nothing keeps reading the old shape.
- **Item 5 (`observer.read` → `record_read`):** no alias, no deprecation period.
- **Item 6 (dispatch policy):** `classify_value`'s current `numeric_array → ChannelStore` branch can just be deleted for the `observe`/`verify` path. No legacy preservation.
- **Item 12 (serialization registry):** pickle-fallback warning behavior is greenfield — pick what's right, no need to match `save_ref_to_dir`'s current silence.
- **Item 14 (typed leaf-types):** scalar `int` was being silently cast to `float64`; that's a bug, fix it without considering "what about code that depended on the cast?"
- **Item 17 (rename `properties` → `attributes`):** mechanical rename, no shim.
- **Item 21 (typed Arrow event payloads):** the JSON payload column can just be replaced. Existing consumers that read JSON via `json_extract_string(...)` get updated to typed access; no dual-read path.

The only backcompat constraints that DO bind v0.2.0:
- Public-facing API surfaces *that work today and external code calls* (verify, observe, the LitmusClient public methods that pre-0.2 users rely on for scripted runs).
- On-disk semantics for behaviors documented as stable (run parquet column meanings for measurements that pass/fail/error correctly today — i.e., the analytical surface).

Everything else is a bug fix, not a contract break.

### Milestones (PR clusters within the stage branch)

These cluster related PRs so progress is visible during the multi-month landing. Each milestone is multiple PRs **into the stage branch**; none of them is a separate release.

| Milestone | Cluster of items | Demo-able outcome |
|---|---|---|
| **M1 — FileStore foundation** | 1a, 1b, 1c, 1d, 1e, 2, 3 | blob captures land in FileStore; image-drop fixed; `_ref` unified; streaming sink works |
| **M2 — Verb consistency** | 4, 5 (+ observer.read rename), 6, 7, 8 | three verbs work with consistent dispatch; symmetric channels/filestore APIs; observations traceable |
| **M3 — Materialization rules** | 9, 10 | pure-characterization runs produce parquet rows; type-stable kind-registry enforced |
| **M4 — Schemas, types, serialization** | 11, 12, 13, 14, 15, 16, 17 | `received_at`/`acquired_at` schemas; typed leaf-types end-to-end; `XYData` model; namespace argument; metadata fields named `attributes` consistently |
| **M5 — Live + perf** | 18, 19, 20 | live waveform plot; perf bench validates §2 estimates; Consumer SDK (async) ships |
| **M6 — Payload + transport refactors** | 21, 22 | typed Arrow event payloads (read-side win); local shared-memory transport |
| **M7 — Video performance** | 23, 24 | hardware video encode + decode |
| **M8 — Documentation rollup** | 25, 26, 27, 28, 29, 30 | operator-UI doc drift cleared; public docs reflect four-store + three-verb model; CHANGELOG complete |

Milestones can run in parallel where they don't depend on each other (M5 can start once M1 lands; M8 doc work can run alongside any code milestone for the pages it depends on).

### Calendar honesty

Multi-month landing. Realistic execution depending on focus is 4-6 months of part-time work or 2-3 months of focused work, plus the perf refactors (items 21-22) which are substantial in their own right. The tag fires when M1-M8 are all merged into the stage branch AND the stage branch has merged into main — not on a calendar date.

### Why "all or nothing for the tag"

Pre-1.0 audiences should see one release where data architecture stabilizes — not a string of releases where the data layer rolls in piecemeal and the docs always lag a version. **v0.2.0 is when "this is what Litmus does with your test data" becomes a settled story for adopters to build on.** Tagging early means adopters integrate against a half-built model and have to re-integrate when the rest lands. The tag IS the integration commitment.

Patches (0.2.1, 0.2.2, …) absorb bug fixes after the tag. Long-term items (L1; possibly things from §15 if they get prioritized) land in v0.3.0 or later.

---

## 17. Server deployment + backend swappability

The architecture is **store-abstract at the user surface**: verbs (`observe` / `verify` / `stream`) don't know about backends. So a backend swap doesn't change user code — but it does need work behind the verbs. **Server-ready ≠ server-deployed.** This section covers what's already designed for it, what's a clean swap, and what operational work is needed before server deployment is realistic.

### What's server-ready today

Every store already has a transport that could front-end a remote backend:

| Store | Already uses | Already supports |
|---|---|---|
| EventStore | Arrow Flight `do_put` / `do_get` | client/server transport (Flight is the protocol) |
| ChannelStore | Arrow Flight `do_put` / `do_get` | same |
| FileStore | local FS today; fsspec is the planned abstraction | remote backends via fsspec (S3, GCS, Azure, NAS) |
| ParquetBackend | local parquet + DuckDB | DuckDB-over-S3 via httpfs extension |

User-facing test code doesn't change between local and server modes:

```python
# Test code identical whether backend is local or remote
verify("vout", dmm.measure_voltage(), Limit(low=3.2, high=3.4))
observe("scope.ch1.capture", scope.acquire())
stream("iv_curve.i", dmm.read_current())
```

What changes is **project configuration** (`litmus.yaml`) — pointing each store at a local or remote endpoint:

```yaml
# Local (today)
data_dir: ./data

# Remote / server mode
event_store:
  flight_url: grpc://litmus-events.internal:8815
channel_store:
  flight_url: grpc://litmus-channels.internal:8816
file_store:
  backend: s3://my-bucket/litmus/files
parquet_backend:
  backend: s3://my-bucket/litmus/runs
```

### Per-store backend alternatives

Each store has natural backend choices; cross-mixing is fine:

| Store | Natural fits | Awkward / poor fit |
|---|---|---|
| **EventStore** | Kafka (it IS an event log); EventStoreDB; ClickHouse (high-volume OLAP); Postgres with `LISTEN/NOTIFY` | DynamoDB (cost at high event rates); Redis (no good archival) |
| **ChannelStore** | InfluxDB, TimescaleDB, kdb+, Prometheus (push gateway), ClickHouse, AWS Timestream | S3 (no live subscribe); DynamoDB (wrong query model) |
| **FileStore** | S3, GCS, Azure Blob, MinIO, R2, NFS, NAS — all via fsspec | DynamoDB (not file storage); Redis (memory-only) |
| **ParquetBackend** | Snowflake, BigQuery, Databricks/Delta Lake, Apache Iceberg, ClickHouse | DynamoDB (not analytical); Redis (not analytical) |

### Realistic deployment shapes

| Profile | Backends |
|---|---|
| **All-local single machine** (today) | local Arrow IPC + DuckDB + local FS |
| **All-cloud, single project** | Kafka events + ClickHouse channels + S3 files + Snowflake parquet |
| **Hybrid: live local, archive remote** | local Arrow IPC + DuckDB (live) + S3 for FileStore archive + Snowflake for analytics |
| **Compliance-heavy (regulated industry)** | local writes + encrypted S3 + on-prem Postgres + audit-only access |
| **Multi-bench enterprise** | one shared event/channel/file infrastructure; benches are clients writing to it |

### What lift it takes to make swap real

| Lift | Today | After swap-ready |
|---|---|---|
| Per-store abstract interface (Protocol or ABC) | concrete implementations | typed interface; concrete is one of N backends |
| Per-backend adapter | n/a | one adapter class per backend (`KafkaEventStore`, `S3FileStore`, `TimescaleChannelStore`, etc.) |
| Config-driven backend selection | hardcoded local paths | `backend: kafka` / `backend: s3` per store in `litmus.yaml` |
| Auth / IAM | local-trust assumptions | per-backend; varies by service (Flight auth handlers; S3 IAM; Kafka SASL) |
| Service discovery | hardcoded / env | DNS / Consul / Kubernetes / configuration |
| Encryption in transit | optional | mandatory (TLS for Flight; HTTPS for HTTP-based) |
| Tenant isolation | n/a (one project per machine) | partitioning / namespacing in shared backends |
| Network failure handling | n/a | retry + buffering + circuit-breaker policy |
| Consistency model surfacing | n/a (local strict) | document per-backend consistency to consumers |

### What server mode loses

The **build item 22 local shared-memory transport** doesn't apply in server mode — it's specifically for same-machine processes. Server deployments pay the network cost; local deployments get the shm fast-path. Both ride the same verbs, just different transport profiles.

### Why this isn't v0.2.0

v0.2.0 is about the **data architecture itself** — getting the stores, verbs, dispatch, and lifecycle right. Backend swap is a **v0.3.0 / v0.4.0 lift** when remote/server deployment becomes a real part goal. The architecture is *ready* for it (user code won't change when the lift lands); the *implementation work* is a per-backend engineering investment that doesn't block the v0.2.0 design.

What v0.2.0 does establish: the abstractions (Flight for typed-row stores; fsspec for files) that make the eventual swap not a rewrite. So v0.2.0 work is preparatory even though it doesn't deliver server mode itself.

---

## 18. Non-goals for v0.2.0

What v0.2.0 **explicitly does not include**, consolidated here so future contributors don't re-litigate. Each is a deliberate scope decision, not an oversight.

### Architectural non-goals (won't do for design reasons)

- **Search by raw time-series values** — never a goal. Not an industry pattern. Search by derived attributes/stats (per L1) is the goal, deferred to post-v0.2.0.
- **Struct / composite types in ChannelStore** — explicitly no `pa.struct<…>` rows. For paired streams (XY, complex), use two related channels (Pattern A in §4). For composite captures, use a model → FileStore (Pattern B). Keeps ChannelStore uniformly typed.
- **Native complex dtype in ChannelStore** — use two parallel channels (real, imag) for streaming complex; use numpy complex via FileStore (`.npz`) for discrete captures. Arrow's complex extension types aren't worth the maturity cost for the marginal ergonomic gain.
- **Sample-rate / dt promotion to descriptor** — Waveform-specific fields stay on the Waveform model; ChannelStore descriptor remains uniform across all channel shapes. Revisit only if a real typed-field-query use case lands.
- **Per-row struct values** as anything richer than typed leaf primitives — `bool`/`int`/`float`/`str` for scalars; lists of same for arrays. No nested structures. Discipline keeps the schema interpretable.

### Scope non-goals (won't do for "this isn't the release for it" reasons)

- **Backend swap implementation** (Kafka events / S3 files / Snowflake parquet / etc.) — architecture is ready (verbs are backend-agnostic); per-backend adapter work is v0.3.0+ when remote deployment becomes a part goal. See §17.
- **`litmus migrate` tool from 0.1.x** — pre-1.0, no backcompat. `rm -rf data/` is the migration. Documented in `MIGRATION.md`. See §16.
- **Project-scoped or station-scoped channel registry** — v0.2.0 commits to global per channel_id. Revisit in v0.3.x if real adopters hit cross-project / cross-station collisions. See §15 open item.
- **Station-level environmental daemon** — per-process metadata production today; daemon pattern is a clean future move (v0.2.x / v0.3.x). See §15 open item.
- **Per-store attribute indexes (L1)** — genuinely greenfield index work; deferred to v0.3.0+ when adopters demonstrate the use case at scale.
- **Typed Flight transport** (vs today's utf8 + JSON encode_value) — works today; performance optimization deferred. Would be L8-class long-term work.
- **Auth / IAM / encryption** for server-mode Flight + HTTP — operational concerns for server deployment, not v0.2.0 architecture work. See §17.

### Behavior non-goals (won't do for "user expectations" reasons)

- **Auto-association of `stream` calls with the active vector** — `stream` and `observe` stay strictly orthogonal. Author calls `observe(name, ch)` explicitly to associate a streamed channel with a vector. Avoids surprising "which vector did this auto-stamp" behavior across vector boundaries.
- **`out_*` overwrite warning on repeated stamps** — last-write-wins is the rule for vector context. Author intentionally stamps multiple times in some workflows (multi-stage captures). No noisy warning.
- **Cross-session channel data merging at query time** — channel rows are session-tagged; readers explicitly join by session if they want cross-session aggregation. No implicit "all sessions of voltage" view.
- **Per-event-type events.py:* validators** that block "unusual" combinations — events are intentionally permissive at write time. Validation happens at consumer / materializer / parquet boundaries, not at emit.

### What this means for adopters reading the doc

If you're trying to do something on this list, you're correctly identifying a real need that v0.2.0 doesn't address. The deferrals aren't "we forgot" — they're scoped out deliberately so v0.2.0 can ship. Most have a clear v0.3.0+ path. A few (raw-value search, per-row structs) are permanent non-goals.

---

## Sources

**Software-architecture patterns**
- Claim-check — [Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/patterns/claim-check), [Spring Integration](https://github.com/spring-projects/spring-integration/blob/v6.4.1/src/reference/antora/modules/ROOT/pages/claim-check.adoc), [EIP / Software Patterns Lexicon](https://softwarepatternslexicon.com/enterprise-integration-patterns/message-transformation/claim-check/)
- Event sourcing — Martin Fowler, ["Event Sourcing"](https://martinfowler.com/eaaDev/EventSourcing.html) (2005); [EventStoreDB](https://www.eventstore.com/)
- CQRS — Greg Young, ["CQRS Documents"](https://cqrs.files.wordpress.com/2010/11/cqrs_documents.pdf) (2010)
- Kafka log-as-database + `LogAppendTime` — [Jay Kreps, "The Log"](https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying)

**ML experiment tracking**
- MLflow backend-store vs artifact-store + multipart upload — [Artifact Stores](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/), [Backend Stores](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/), [`log_artifact`](https://mlflow.org/docs/latest/python_api/mlflow.html#mlflow.log_artifact)
- W&B Artifacts — [docs.wandb.ai/guides/artifacts](https://docs.wandb.ai/guides/artifacts/)

**Time-series + streaming infrastructure**
- Apache Arrow + Flight — [Arrow project](https://arrow.apache.org/), [Arrow Flight](https://arrow.apache.org/docs/format/Flight.html)
- InfluxDB / TimescaleDB / Prometheus / kdb+ — canonical append-only time-series stores

**T&M-native systems (ATML actually consulted)**
- IEEE 1671 ATML — test result format, hierarchy, traceability columns
- TDMS internal structure (file / group / channel + properties; waveform = start + increment + array) — [NI: TDMS File Format Internal Structure](https://www.ni.com/en/support/documentation/supplemental/07/tdms-file-format-internal-structure.html), [npTDMS](https://nptdms.readthedocs.io/en/stable/reading.html)
- OpenHTF — [google/openhtf on GitHub](https://github.com/google/openhtf)
- HDF5 — [hdfgroup.org](https://www.hdfgroup.org/solutions/hdf5/)
- ISO/IEC 17025 — calibration and traceability in T&M labs
- AIAG MSA — measurement system analysis (Cpk, Gauge R&R)

**Observability**
- OpenTelemetry — [opentelemetry.io/docs/concepts/](https://opentelemetry.io/docs/concepts/)

**Web standards**
- W3C Media Fragments URI (`#t=` syntax) — [w3.org/TR/media-frags/](https://www.w3.org/TR/media-frags/)
- HTTP range requests — [RFC 7233](https://datatracker.ietf.org/doc/html/rfc7233)

**Pluggable filesystem + format libraries**
- fsspec — [filesystem-spec.readthedocs.io](https://filesystem-spec.readthedocs.io/)
- PyAV (ffmpeg bindings) — [pyav.org](https://pyav.org/)
- soundfile (libsndfile) — [python-soundfile.readthedocs.io](https://python-soundfile.readthedocs.io/)
- tifffile — [github.com/cgohlke/tifffile](https://github.com/cgohlke/tifffile)
- h5py — [h5py.org](https://www.h5py.org/)
- Pillow — [python-pillow.org](https://python-pillow.org/)
