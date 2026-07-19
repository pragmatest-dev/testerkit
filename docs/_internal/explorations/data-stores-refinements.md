# Data architecture refinements: verbs, dispatch, types, and consumer surface

**Status:** continuation of [`data-stores-claim-check.md`](data-stores-claim-check.md). Captures iteration on the user-facing API model + schema details + the consumer SDK shape that came out of design conversation after the baseline note landed. Several positions in the baseline are **superseded** here — flagged inline.

**Audience:** contributors. Internal — file:line citations and internal class names fine here. Do not link from public docs. Read alongside the baseline.

---

## What changed from v1

| Topic | v1 baseline | v1.1 refinement | Why |
|---|---|---|---|
| User surface verbs | two: `observe`, `verify` | **three: `observe`, `verify`, `stream`** | test code can legitimately stream continuous numerics; needed a verb for it |
| `observe` value types | scalar or non-scalar (file) | **scalar, channel-shaped numeric, file-shaped blob, OR a reference (channel handle / file URI / Path)** | the v1 dispatch over-routed everything to FileStore; channel-shaped data belongs in ChannelStore |
| ChannelStore routing rule | "ChannelStore reached only via `observer.read`" | ChannelStore reached via `observer.read`, `channels.write`/`stream`, **AND** via `observe`/`verify` when the value is channel-shaped | drivers aren't the only producers of live numerics |
| Waveform routing | → FileStore (.npz) | → ChannelStore (ARRAY_SCHEMA row) | `t0`/`dt`/`Y` is structurally what ChannelStore is for; .npz fallback only at archival via copy-on-prune |
| Streaming verbs | one `filestore.stream(...)` sketch | **symmetric per store**: `channels.write/stream` + `filestore.put/stream` | producer ergonomics + context preserved at call site |
| Auto-association of streams | "auto-observe on first write per vector" floated | **dropped** — `stream` and `observe` are strictly orthogonal | streams genuinely span vectors / sessions; auto-association would surprise |
| Row timestamp column | `timestamp` (ambiguous) | **rename `timestamp` → `received_at`; add nullable `acquired_at`** | consistency with EventStore envelope; capture instrument-provided times when available |
| FileStore typing | "attributes captured at put" (vague) | **MIME type + extension + attributes dict**, reusing existing format libraries | universal standard; don't reinvent encoders |
| Consumer surface | implicit (UIs read events + Flight + range-read directly) | **new `testerkit.live` SDK** wrapping all three subscription primitives | give consumers one ergonomic entry point |

The remainder of this note expands each.

---

## The user surface — three verbs, strict orthogonality

Test authors write **three verbs**; driver authors keep `observer.read` (still its own verb, naming smell flagged below); power users reach the operational store APIs explicitly.

| Verb | Audience | Concern | Touches store? |
|---|---|---|---|
| `observe(name, X)` | test author | "associate X with my vector at this moment" | dispatches by type (see below) |
| `verify(name, X, limit=None)` | test author | observe + emit a measurement row (judged if limit) | same dispatch as observe |
| `stream(name, sample)` (sugar over `channels.write`) | test author | "push one sample into a continuous record" | **always** ChannelStore; never auto-associates |
| `observer.read(channel, value, method)` | driver author | recording call: writes channel + emits `InstrumentRead` | always ChannelStore + EventStore (rename candidate, see below) |
| `channels.write(name, sample)` / `with channels.stream(name) as ch:` | power user | direct ChannelStore access | ChannelStore |
| `filestore.put(name, value)` / `with filestore.stream(name, format=…) as sink:` | power user | direct FileStore access | FileStore |

**Key shift:** `stream` and `observe` are **strictly orthogonal** — no implicit coupling.

- `stream(name, sample)` writes to a channel. **Never stamps `out_*`. Never observes.**
- `observe(name, channel_handle)` is how you associate a channel with a vector. Explicit, every time.

Reason: streams legitimately span vectors (background recorders, fixture loggers, multi-run captures). Auto-association on first write surprises in non-vector-scoped cases. Verbosity is cheap; surprise behavior is expensive.

Common pattern for vector-scoped streaming:

```python
ch = channels.stream("iv_curve.i")
observe("iv_curve.i", ch)                # explicit: link to this vector
for v in voltages:
    psu.set_voltage(v)
    stream("iv_curve.i", dmm.read_current())
```

Common pattern for session-scoped background recording:

```python
# Opened in conftest setup
ch = channels.stream("operator_camera")     # no observe — not vector-associated

# In a specific test that wants to claim it:
def test_thing(camera_channel):
    observe("operator_camera", camera_channel)
    verify("vout", dmm.read(), Limit(...))
```

---

## Value-type dispatch (sharper than v1)

For `observe(name, X)` / `verify(name, X, limit=…)`:

| Value type | Destination | What `out_<name>` carries |
|---|---|---|
| Scalar (`float`/`int`/`bool`/`str`) | inline in event payload | the scalar |
| **Channel-shaped numeric** (`Waveform`, `numpy.ndarray` of numerics, scalar stream) | **ChannelStore** (ARRAY_SCHEMA row per call) | `channel://…?session=…` |
| **File-shaped** (`Path`, `bytes`, `PIL.Image`, video frame, vendor blob, `BaseModel`, `DataFrame`) | **FileStore** (one artifact per call) | `file://…` |
| Channel handle / `channel://` URI | (no re-write) stamp the claim | `channel://…` |
| File URI / `file://` URI / Path-already-in-FileStore | (no re-store) stamp the claim | `file://…` |

> **Supersedes v1:** the baseline dispatch was "non-scalar → FileStore always." That over-routed `Waveform`/`ndarray` to file form, losing the live-subscribable + per-sample-queryable benefits of ChannelStore.

### Why channel-shaped vs file-shaped, not "type sniffing by Python class"

The same Python object can sometimes fit either store. The rule isn't *Python type alone*; it's whether the value has channel-shape semantics (typed numerics with implicit per-sample time) or file-shape semantics (arbitrary bytes / format).

- `numpy.ndarray` of `int16` from a DAQ buffer → channel-shaped, ChannelStore
- `numpy.ndarray` from a Pillow image (`np.array(img)`) → file-shaped really; better to pass the `Image` object directly so the registry picks PNG

The verbs are deliberately polymorphic on type for **intent verbs** (`observe`, `verify`); the routing follows from the value's class via the serialization registry. Edge cases are handled by passing the value in the form that matches the intent (`Image` → file; `Waveform` → channel).

### Why streaming verbs are NOT polymorphic — operational vs intent

`channels.stream(name)` and `filestore.stream(name, format=…)` are **explicit per store**, not dispatched by type. Their operational shapes are too different:

| | Channel stream | File stream |
|---|---|---|
| Granularity | one typed sample per call | bytes per chunk |
| Lifecycle | append-only, no close needed per sample | open / write / close |
| Live subscribers see | each row via Flight `do_get` | partial bytes via HTTP range + frame-index events |
| Throughput model | per-sample notify, segment-rotated files | byte-aware buffering, format-specific encoder |

Forcing one verb means the author still has to know which mode (`stream("name", scalar)` vs `with stream("name", format="mp4") as sink:`), and you've spent the simplicity budget hiding a distinction that isn't actually hidden. Polymorphic dispatch lives in **intent verbs only**; **operational verbs are explicit per store**.

---

## ChannelStore schema refinements

> **Supersedes v1's implicit "timestamp" column.**

### Rename `timestamp` → `received_at`

```python
SCALAR_SCHEMA = pa.schema([
    ("acquired_at", pa.timestamp("us", tz="UTC"), nullable=True),   # instrument's clock when provided
    ("received_at", pa.timestamp("us", tz="UTC")),                  # platform wall-clock at write
    ("value", pa.float64()),
    ("source_method", pa.utf8()),
    ("session_id", pa.utf8()),
])

ARRAY_SCHEMA = pa.schema([
    ("acquired_at", pa.timestamp("us", tz="UTC"), nullable=True),   # instrument's t0 (Waveform.t0 lands here)
    ("received_at", pa.timestamp("us", tz="UTC")),                  # platform wall-clock at write
    ("samples", pa.list_(pa.float64())),
    ("sample_interval", pa.float64()),                              # per-acquisition spacing (was already here)
    ("source_method", pa.utf8()),
    ("session_id", pa.utf8()),
])
```

Two timestamps per row, distinct meanings:

| Column | Meaning | Source | Presence |
|---|---|---|---|
| `acquired_at` | when the data was acquired on the instrument's clock | instrument-provided when available; null otherwise | nullable |
| `received_at` | when the row was written to ChannelStore | always platform wall-clock | required |

**Consistency across stores** (the `verb_at` style is now uniform):

| Store | Layer | Timestamp(s) | Style |
|---|---|---|---|
| EventStore envelope | event arrival | `received_at` | `verb_at`, platform clock |
| ChannelStore row | data write | `received_at` | `verb_at`, platform clock |
| ChannelStore row | data origin (when known) | `acquired_at` | `verb_at`, instrument clock |
| FileStore artifact | (future) | `created_at` | `verb_at`, platform clock |

Prior art: this is exactly Kafka's `LogAppendTime` + creator-provided `Timestamp` shape. Same problem (storage-time vs source-time), same solution. Used for clock drift detection, latency analysis, audit, and consistent ordering for live subscribers.

### Don't promote waveform-specific fields to ChannelDescriptor

> **Supersedes the "promote `sample_rate` / `dt` / `t0` to typed descriptor fields" position from earlier turns.**

Reasons it would have been wrong:
- ChannelStore holds streams of *many* shapes (DMM scalars, irregular thermocouples, computed derived streams). Most don't have a fixed `sample_rate`.
- Polluting the universal schema with Waveform-specific terminology imposes one shape's vocabulary on everything.
- Waveform's intrinsic fields (`t0`, `dt`, `Y`, `attrs`) belong on the **Waveform model** itself, not on the store.

When a `Waveform` lands in ARRAY_SCHEMA:
- `wf.Y` → `samples`
- `wf.dt` → `sample_interval` (per-row, varies per acquisition — was always there)
- `wf.t0` → `acquired_at` (the data origin time — single nullable column finally has a home for it)
- `wf.attrs` → `properties` on the descriptor OR inlined in the value (TBD; not blocking)

So Waveform round-trips cleanly through ChannelStore using existing + minimally-added schema, without imposing waveform vocabulary on every channel. The descriptor's `properties: dict[str, Any]` (already exists) carries channel-level metadata for projects that need it; **no typed promotions in v1**.

---

## Channel / run association — still session-scoped

> Unchanged from v1, but worth re-stating because the question came up.

Channels are **session-scoped at the data-row level**; run association is **mediated via URIs** in events and parquet, not stamped on channels.

| Where it lives for a channel | What it carries |
|---|---|
| `ChannelDescriptor` (global) | kind-level: `channel_id`, `data_type`, `units`, `properties` — **no session_id, no run_id** |
| Data rows | `session_id` column |
| Filename | `channels/{date}/{channel_id}_{session_short}.arrow` |
| Claim URI | `channel://{channel_id}?session={session_id}` — session-bearing |
| Run association | only via the event log (`InstrumentRead.run_id` ↔ channel URI in payload) and via parquet `out_*` columns |

Why no `run_id` on channel rows:
- Channels legitimately span runs (fixture-temp logger across run 1, run 2, run 3)
- Channels can exist outside runs (calibration, setup, idle monitoring)
- Run scoping for a stream is the exception; URI-mediated association via events handles it without forcing the model

Channel number stays in the name (`scope.ch1`, `daq.ai0`, etc.). Don't promote to a typed `channel_number` field — the concept doesn't fit uniformly across instrument families (DMMs don't have "channel numbers" the way scopes do).

---

## FileStore typing — MIME + extension + format libraries

### MIME as primary identifier

FileStore artifact metadata carries three identification layers:

```python
class FileArtifactMetadata:
    file_uri: str                  # "file://_ref/uut_video.mp4"
    mime_type: str                 # "video/mp4" — primary dispatch field
    extension: str                 # ".mp4" — fallback when MIME is ambiguous
    size: int
    session_id: str
    created_at: datetime
    original_filename: str | None  # preserved on Path-copy
    attributes: dict[str, Any]     # format-specific extras: width/height, duration, codec, …
```

Standard IANA types cover most cases. T&M-specific formats need a small TesterKit convention table for vendor / scientific types without registered MIMEs:

| Format | MIME |
|---|---|
| PNG / JPEG | standard (`image/png`, `image/jpeg`) |
| MP4 / WebM | standard (`video/mp4`, `video/webm`) |
| PDF / JSON / CSV / Parquet | standard |
| NPZ | `application/x-numpy-archive` (de-facto) |
| NPY | `application/x-numpy` |
| TDMS | `application/vnd.ni.tdms` |
| Pickle | `application/x-python-pickle` |

Dispatch by MIME family handles most rendering:

```python
def render(artifact):
    mime = artifact.mime_type
    if mime.startswith("image/"):   return ImageRenderer(...)
    if mime.startswith("video/"):   return VideoPlayer(...)
    if mime.startswith("audio/"):   return AudioPlayer(...)
    if mime == "application/pdf":   return PDFViewer(...)
    if mime == "application/x-numpy-archive":  return WaveformPlotter(...)
    # … else: DownloadOnly
```

### Reuse format libraries — FileStore is orchestration only

The FileStore owns artifact **identity** (URI, location, metadata, lifecycle events). Format **encoding/decoding** belongs to existing well-tested libraries. Do not reinvent video encoders, audio codecs, TDMS writers, etc.

The serializer registry extends to two interfaces per format:

```python
class FormatHandler(Protocol):
    def put(self, value: Any, dest: Path) -> Path: ...                       # one-shot
    def open_writer(self, dest: Path, **opts) -> StreamingSink: ...          # streaming
    def detect_attributes(self, dest: Path) -> dict[str, Any]: ...           # metadata extraction at close
```

Recommended library-by-format wrappers:

| Format | Library | Notes |
|---|---|---|
| MP4 / H.264 / WebM | **PyAV** (ffmpeg bindings) | full codec coverage |
| WAV / FLAC / OGG | **soundfile** (libsndfile) | scientific/audio standard |
| Multi-frame TIFF | **tifffile** | BigTIFF, OME-TIFF |
| PNG / GIF / animated PNG | **Pillow** | already in registry |
| TDMS | **nptdms** | `TdmsWriter` is chunk-friendly |
| HDF5 | **h5py** | chunked datasets |
| Parquet | **pyarrow** | already in stack |
| Pickle (fallback) | stdlib | with RuntimeWarning |

Each handler wraps an existing library's writer. FileStore handles path allocation, lifecycle events (`FileStarted`/`StreamFrameIndex`/`FileEnded`), metadata capture at close, and claim URI. The library handles encoding.

---

## Live → archival lifecycle (ChannelStore vs FileStore framing)

The two stores are **two lifetime phases of the same continuous data**, not parallel competitors:

| Phase | Store | Optimized for | Lifetime |
|---|---|---|---|
| **Live working memory** | ChannelStore | per-sample subscribe, per-sample query, high-rate writes | days to weeks (retention-windowed) |
| **Durable archive** | FileStore | self-contained run, portable, render/download, long-term retention | indefinite |
| **Bridge** | `materialize_channel_refs` (copy-on-prune) | promote run-referenced channel data to file form before pruning | retention-triggered |

So a Waveform written via `observe("scope.ch1.capture", wf)`:
1. Lands as a ChannelStore row immediately → live + subscribable + per-sample queryable.
2. `out_scope.ch1.capture = channel://…?session=…` stamped on the vector at materialization.
3. Eventually (retention prune) → channel data copies into `runs/_ref/` as `.npz` (Waveform round-tripped via nptdms / np savez).
4. Parquet URI rewrites `channel://` → `file://…/scope.ch1.capture.npz`.
5. Same waveform, archival form, run still self-contained.

Author never sees the lifecycle — they call `observe(name, wf)` and the system handles it.

### Why this doesn't collapse to MLflow's artifact-store shape

MLflow has metadata DB + artifact store. No equivalent of ChannelStore (live high-frequency typed numeric streaming). If we dropped ChannelStore we'd lose:

- Live waveform plots (can't subscribe per-sample to a file)
- Per-sample windowed queries on multi-Hz data (file = open + parse + filter in memory)
- High-rate writes (1kHz+ samples through file-writer semantics either flush-per-write or batch-until-close)

Channels earn their weight specifically for **live + queryable + high-rate**. Drop those and the architecture collapses to MLflow-shaped. Keep them and we have a tier MLflow doesn't.

---

## Consumer SDK — `testerkit.live`

> **New surface.** v1 had "events as the spine" abstractly; this gives consumers (UIs, MCP tools, external integrations, custom dashboards) one place to subscribe / dereference, regardless of underlying store.

### What it hides vs doesn't

**Hides:**
- Protocol selection (Flight `do_get` vs HTTP range)
- URI parsing (`channel://` vs `file://`)
- Event JSON payload deserialization → typed event objects
- Lookup-by-name (`subscribe("scope.ch1")` resolves to the right store)

**Doesn't hide (because real):**
- Data shape (samples vs bytes vs structured events) — consumer handles three render types

### Three subscription primitives

```python
from testerkit.live import LiveClient, EventFilter

client = LiveClient.connect(url="http://localhost:8000")

# 1. Event subscription — the timeline routing layer
async for ev in client.subscribe_events(EventFilter(run_id="abc123")):
    if isinstance(ev, MeasurementEvent):
        table.add(ev.name, ev.value, ev.outcome)
    elif isinstance(ev, ObservationEvent):
        if ev.claim and ev.claim.scheme == "file":
            viewer.render(ev.claim.deref())
        elif ev.claim and ev.claim.scheme == "channel":
            ch_sub = client.subscribe_channel(ev.claim.channel_id)
            ...

# 2. Channel subscription — per-sample push
async for batch in client.subscribe_channel("scope.ch1"):
    plot.append(batch.samples)

# 3. File subscription — partial reads with frame-index notifications
async for chunk in client.subscribe_file("file://_ref/uut_video.mp4", live=True):
    video_decoder.feed(chunk.bytes)

# 4. Convenience: subscribe to a whole run's live updates
async for update in client.subscribe_run_live("abc123"):
    update.dispatch(
        on_measurement=lambda m: table.add(m),
        on_channel_batch=lambda b: plots[b.channel_id].append(b.samples),
        on_file_chunk=lambda c: viewers[c.file_uri].update(c.bytes),
    )

# 5. Resolver — one-shot fetch any claim URI
data = client.deref("channel://scope.ch1?session=…", window=(-30, 0))
img = client.deref("file://_ref/front.png")
```

### Where it fits

| Layer | API | Audience |
|---|---|---|
| Stores | `ChannelStore.write/query`, `FileStore.put/range_read`, `EventLog.emit` | platform-internal |
| Verbs (writes) | `observe` / `verify` / `stream`, `observer.read`, `channels.*`, `filestore.*` | producers |
| **Consumer SDK (reads)** | `testerkit.live.LiveClient` | **consumers** |
| Transport | Arrow Flight, HTTP + range, IPC | platform-internal |

The SDK is the read-side counterpart to the verbs. Verbs make writing ergonomic without exposing the stores; the SDK makes reading ergonomic without exposing the stores.

---

## Manifestation rules — unchanged from v1

The auto-promotion rule, `out_*` vector-grain denormalization, type-stable kind-registry, explicit-override-via-`verify-of-non-scalar`, and the "cost worth naming" (row-shape shifts when characterization → mixed) are all unchanged. See [`data-stores-claim-check.md` → Manifestation rules](data-stores-claim-check.md).

---

## Updated build items (delta from v1)

The baseline's items 1–9 (MVP) and 10–12 (long-term) still stand. This adds / modifies:

### New MVP items

13. **`stream(...)` verb** as the test-author-facing channel-write sugar (over `channels.write`). One-line append-a-sample to a channel; no implicit vector association.
14. **Symmetric streaming verbs across stores** — `channels.write` / `channels.stream` + `filestore.put` / `filestore.stream`. Both context-manager and one-shot forms per store. Explicit per store; no auto-dispatch by type.
15. **`observe` / `verify` accept references** — channel handle, file URI, Path-already-in-FileStore. Recognize and stamp the claim without re-write/re-store.
16. **Sharpen the dispatch policy** (modifies baseline item 6): channel-shaped numerics → ChannelStore; arbitrary bytes/formats → FileStore. `Waveform` → ChannelStore as ARRAY_SCHEMA row.
17. **Rename ChannelStore row `timestamp` → `received_at`**; add nullable `acquired_at`. Pre-1.0 mechanical rename + one new column on both schemas.
18. **FileStore typing: MIME + extension + attributes dict**. Add the TesterKit MIME convention table for vendor formats (NPZ, NPY, TDMS, pickle).
19. **Format library reuse for FileStore streaming sinks.** Wrap PyAV / soundfile / tifffile / nptdms / h5py / pyarrow rather than implementing encoders. Extend the serializer registry contract with `open_writer(...)` for streaming.

### New long-term items

20. **Consumer SDK (`testerkit.live`)** — typed event objects, `subscribe_events` / `subscribe_channel` / `subscribe_file` / `subscribe_run_live` / `deref`. Hides transport + URI dispatch; doesn't hide data shape.

### Naming smell flagged (not blocking)

- **`observer.read` is a writing API misleadingly named "read".** It takes a value the caller already read from the instrument and (1) writes it to ChannelStore and (2) emits an `InstrumentRead` event. The "read" refers to *the instrument* having been read; the framework's call is the recording call. Rename candidate: `record_read` / `record_sample` / `report_read`. Pre-1.0, cheap rename, doesn't change the model.

---

## Naming style across stores (summary)

| Concept | Style | Example |
|---|---|---|
| Wall-clock timestamps (platform-side) | `verb_at` past-participle | `received_at`, `created_at` |
| Source-clock timestamps (data-side) | `verb_at` past-participle | `acquired_at` |
| T&M-domain attributes (data structure) | T&M-native term | `sample_interval`, Waveform's `t0`, `dt`, `Y`, `attrs` |
| Identifiers | snake_case nouns | `channel_id`, `session_id`, `run_id`, `source_method` |

T&M-domain terminology stays **on domain models** (Waveform), not on universal store schemas. Universal schemas use `verb_at` consistently.

---

## What's still open after this iteration

- **`observer.read` rename** — small change, deferred until someone touches the file.
- **`acquired_at` source semantics** — what does "instrument time" mean precisely for each driver path? Some instruments don't have a clock; some have one but don't expose it through the wrapper. Per-driver decisions; not blocking the schema change.
- **`Waveform.attrs` landing spot** — descriptor `properties` vs row-inlined. Defer; not blocking.
- **Consumer SDK details** — actual class layout, async-vs-sync, transport details. Sketch above is the shape; implementation needs a real spike.
- **Channels.stream context-manager handle shape** — exact methods (`.write(sample)`, `.close()`, `.flush()` perhaps). Bikeshed-friendly, defer.
- **Sample-rate / dt promotion to descriptor** — explicitly **not** doing in v1; revisit if a real use case lands that needs typed-field queryability (`SELECT channels WHERE sample_rate > 1e6` style).

---

## Audit anchor

The v1 baseline note has a full prior-art audit (post-hoc convergence; ATML the only deliberate input). The refinements here don't change that picture materially:

- **`acquired_at` + `received_at`** — Kafka precedent (LogAppendTime + creator Timestamp). Convergent.
- **Three verbs (observe / verify / stream)** — OTel separates "attributes" / "metrics" / "counters"; MLflow separates `log_param` / `log_metric` / `log_artifact`. Convergent.
- **Format library reuse** — every artifact store does this (MLflow `pyfunc.flavors`, etc.). Convergent.
- **MIME as primary identifier** — universal (HTTP, S3, GCS, object stores). Convergent.
- **Consumer SDK shape** — MLflow client, W&B client, OTel exporter APIs all expose subscribe-style consumer surfaces. Convergent.

ATML stays the only consulted input; everything else is convergence with widely-applied patterns.
