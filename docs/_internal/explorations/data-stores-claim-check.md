# Data architecture: claim-check timeline + tech-appropriate stores

**Status:** exploration / design note. Captures a design session on how
Litmus stores test data — anchored in *what T&M data is for*, then the
stores, the claim-check model, what's consistent, and the gaps. Nothing
here is a commitment; it's the map the next build session starts from.

**Audience:** contributors. Internal — file:line citations and internal
class names are fine here. Do not link from public docs.

---

## The assignment: what T&M test data is for

Test & measurement data is **not telemetry or logs — it is evidence that
bears responsibility**, and it has to serve four jobs at once, for a long
time. Every store/claim/index decision below traces back to one of these:

1. **Disposition (the gate).** Did *this* unit meet spec? Pass/fail, bin,
   ship-or-scrap — per-unit, against limits derived from the part spec.
2. **Traceability & provenance (the defense).** A durable, reconstructable
   record tying a **serial** → the **spec/limits** judged against → the
   **instruments + calibration** that measured it → **station, operator,
   conditions, revision, timestamp**. For RMAs, recalls, audits,
   regulatory. Must stay trustworthy and survive years — you may have to
   *prove* a unit passed long after the bench is gone.
3. **Yield & process control (the population).** Across units/lots/
   stations/time: yield, Pareto, Cpk, drift, retest. Needs the data
   **normalized and queryable** in aggregate.
4. **Diagnosis & characterization (the signal).** When something fails — or
   pre-production, mapping the device across its envelope — the **raw
   waveform/image/trace** is the evidence. Heavy, reached for occasionally,
   but must be **findable**.

Cross-cutting, all four demand the data be **trustworthy** (can't fake or
silently lose — why dirty-git demotes, why calibration gates, why events
are immutable), **complete** (even unrun steps recorded — the step
manifest), and **findable** (worthless at scale otherwise).

**Traceability is the spine.** Every datum must carry, or be joinable to,
the context that makes it *evidence* rather than a bare number. A capture
with no traceable context is diagnostically and legally inert. This is why
parquet denormalizes serial/spec/station/cal/operator/conditions onto each
row — and why "where does a waveform's session/run context live" is a
load-bearing question, not a detail.

### How the architecture maps to the assignment

| Job | Served by |
|---|---|
| Disposition | `verify` / limits / outcome → measurement rows |
| Traceability / evidence | the **immutable event timeline** (source of truth) |
| Yield / SPC | the **normalized parquet** (analysis view) |
| Diagnosis | **channels + files** (raw signal & artifacts) |
| Findability across all | **attributes/stats + per-store indexes** |
| Lean + durable evidence | **claim-check** (heavy data out of the timeline) |

---

## The principle (already the design): claim-check

The event log stays a lean, ordered, immutable timeline; heavy payloads
live in a store suited to their data type; the event carries a small
*reference* (claim ticket), not the bytes. Documented today for numerics
(`docs/concepts/data/three-stores.md:25`: *"the EventStore gets a compact
claim-check URI (`channel://…`) instead of the raw data"*). The model
below is the existing one, made symmetric and named.

### Prior art (consensus, not novelty)

- **Claim-Check (EIP / Azure / Spring Integration):** large payload to an
  external store, reference through the channel, dereference on read.
- **MLflow:** a **backend store** (metadata DB) split from an **artifact
  store** (object storage) *because large data is "less-frequently
  accessed and suited for cheap object storage"*; multipart upload for
  large artifacts (the streaming analog). One-to-one with our
  events/runs ↔ heavy-data split.
- **TDMS / HDF5 (T&M-native):** `file → group → channel`; **properties
  (attributes) attach to any object, raw arrays only to channels**; a
  waveform is `wf_start_offset` (t0) + `wf_increment` (dt) + array.
  Litmus's `Waveform(t0, dt, Y, attrs)` (`models.py:485`) mirrors this,
  and `attrs` ↔ TDMS properties. **TDMS files are searchable *because* of
  those properties (NI indexes them in DIAdem)** — directly relevant to
  the FileStore-attributes decision below.

---

## Current state (audited against source)

### The stores

| Store | On disk | Write API | Role (job) |
|---|---|---|---|
| **EventStore** | `events/{date}/{session_id}.arrow` | `emit()` → per-session `EventLog` → `BufferedIPCWriter`; dual-write IPC + Flight `do_put` to DuckDB | Immutable timeline — **traceability/evidence** |
| **ChannelStore** | `channels/{date}/{channel_id}_{session_short}.arrow` (segment-rotated) | `write()` → `_ChannelWriter(BufferedIPCWriter)` + `_flight_push` | Numerics backing — **diagnosis** (`channel://`) |
| **ParquetBackend** | `runs/{date}/{ts}_{serial}.parquet` | `save_test_run()` | **Materialized analysis view** — **yield/SPC** (derived) |

`BufferedIPCWriter` (`_ipc_writer.py:18`) backs both events and channels —
buffered Arrow IPC, **row-count flush**, segment rotation.

### Routing (`classify_value`, `ref.py:19`)

- `scalar` → inline.
- `numeric_array` / `channel` (dict) → **ChannelStore** (`channel://`).
- `blob` (bytes, `Path`, `Waveform` model, image, `.tdms`) → **rejected by
  ChannelStore** (`store.py:276`) → file ref.

### The coordinated capture path exists

`InstrumentObserver.read()` (`observer.py:74`): one call —
`_store_value()` writes numerics to ChannelStore (returns the `channel://`
URI), then **always** `emit(InstrumentRead(...))`.
`InstrumentRead._serialize_with_claim_check` (`events.py:543`) records
**`min`/`max`/`length`/`sample_interval`** inline for arrays while
claim-checking the samples. So "metadata in events, samples in channel" is
real and already computes the stats.

### `_ref` exists — twice — but isn't a store

`EventLog.save_ref()` (`event_log.py:247`) → `events/{session_id}_ref/`
(live); `ParquetBackend._save_file → save_ref_to_dir`
(`parquet.py:367`) → `runs/{stem}_ref/` (materialization). The simple-put
primitive `save_ref_to_dir` (`_row_helpers.py:484`) handles `Path`→copy
(suffix preserved), `Waveform`→`.npz`(+attrs inline), `bytes`→`.bin`,
model→`.json`, ndarray→`.npy`, else→`.pkl`. Two scattered dirs, no
streaming, no unified identity.

### Scoping reality (the load-bearing detail)

Channels, files, and events are **session-scoped, standalone-capable**.
A run is a *narrower, optional* scope inside a session. This is not
uniform across the data:

- **Channel kind** lives in a **global** `_registry.json`
  (`channels/_registry.json`, merged across all sessions —
  `store.py:677-686`). One `ChannelDescriptor` per `channel_id`, with
  **no `session_id` and no `run_id`** (`models.py:19-28`). The descriptor
  is the channel *kind*; it cannot hold per-occurrence facts (its unused
  `properties` dict could only carry kind-level truths like "always volts").
- **Channel occurrence** lives on the **data rows** — every row has a
  `session_id` column (`models.py:94`, both schemas) — and in the filename
  (`_{session_short}.arrow`).
- **Run association for a channel lives nowhere on the channel.** No
  `run_id` on the descriptor, rows, or filename. The only run tie is the
  `InstrumentRead` event (`run_id` ↔ `channel_id`). "Channel in run X" is
  an **event join.**

| Scope | Where it lives for a channel |
|---|---|
| kind (what is `scope.ch1`) | global `_registry.json` descriptor (session-agnostic) |
| session (this occurrence) | data rows (`session_id` col) + filename (`_{session_short}.arrow`) + **the claim URI** |
| run (was it part of a run) | not stamped on the channel — carried by the session-bearing claim URI that flows into the run's events + parquet rows |

**The claim URI carries the session — that's the linchpin.**
`make_channel_uri` (`ref.py:42`) emits
`channel://{channel_id}?session={session_id}`. That session-bearing URI
rides from the observation into the `InstrumentRead` event and the parquet
`out_*` column. So a run "has" a channel by *referencing its
session-scoped URI*, not by tagging the channel with a run id.

> **Superseded 2026-06-09 — see "The federation model" below.** Copy-on-prune is
> dropped in favor of reference-aware retention. This paragraph is kept for context
> on what `materialize` did and why it's a lateral no-op.

**Materialization is session-keyed and copy-on-prune** (not RunEnded).
`materialize_channel_refs` (`materialize.py`) runs *before channel
pruning* (retention): it collects `(channel_id, session_short)` pairs,
asks the runs DuckDB index `find_channel_refs(session_shorts)` which
parquet rows reference them (no file scan), reads each via
`store.query(channel_id, session_id=session_short)`, writes a `file://`
sidecar into that run-file's `_ref`, and rewrites `channel://` → `file://`.
At RunEnded the parquet still points at the **live** `channel://`; only
*blob observations* go to `_ref` at RunEnded (via `save_test_run`). Two
distinct `_ref`-write moments.

Nuance: channel data is **session-granular, not run-granular** (rows carry
`session_id`, no `run_id`). A run materializes the whole session-channel it
referenced; two runs sharing a session + channel each get a copy. Fine for
one-run-per-session; duplicative otherwise.

ParquetBackend is the one store that **is** run-scoped (per-run parquet),
which is why it currently looks like "the searchable store" — but that's
an accident of which store got an index, not the design.

---

## The gaps

1. **Images/blobs are dropped, not stored (bug-shaped).** `InstrumentRead`
   claim-checks arrays to a channel but for a **blob** falls through to
   `data["value"] = repr(v)` (`events.py`, final serializer branch) —
   *even though `EventLog.save_ref` is right there.* Arrays get a
   claim-check; files don't. **This is the "finally picks up our images"
   fix.** (Cuts directly at Diagnosis: a failure's captured image is
   evidence, and it's being thrown away.)
2. **No streaming sink.** `save_ref_to_dir` writes whole values. The stream
   events exist — `FileStarted(file_id, format, path)`, `FileEnded`,
   `StreamFrameIndex` (`events.py:616`) — the claim vocabulary for
   "video/protocol streaming to a file destination" is defined, but no
   writer backs it.
3. **No live home for produced files.** `_ref` at the RunStore is
   materialization-only; the EventLog `_ref` is live but unused for blobs.
   A produced file during the run is held in-memory as a raw observation —
   won't survive a crash, can't hold a video. (Cuts at Trustworthy +
   Diagnosis.)
4. **`observe()` emits no event.** `Context.observe()` (`harness.py:190`)
   writes arrays to the channel but emits **no event** — so a
   manually-observed capture is undescribed and untraceable. The
   coordinated `observer.read` is the right shape; `observe()` is the
   under-powered one. (Cuts at Traceability.)
5. **Metadata/attribute search is NOT performant — and is run-scoped by
   accident.**
   - The event IPC schema (`event_log.py:31-40`) is envelope columns
     (`event_number`/`event_type`/`session_id`/`received_at`) + a single
     **opaque `json` string** holding the whole payload. So `min`/`max`/
     `units`/`limits`/`channel_id` are inside the JSON → filtering on them
     is a **full scan + per-row JSON parse, no statistics, no pruning.**
   - The only typed, pruned, pushdown-capable index today is the **run
     parquet** (`runs/_index.duckdb`) — but it's **run-scoped**, so
     standalone/transient channels and files (no run) are **unsearchable**.
   - ChannelStore has **no SQL index at all** (Flight streaming +
     decimation reader). FileStore (`_ref`) has none. So "channels in run
     X where max > Y" = join events → open each IPC → scan. No index.

---

## Proposed model: three source stores + a materialized view

The missing layer is **raw data persistence and access** — durable
storage for the heavy artifacts a test produces (images, videos,
`.tdms`, `.npz`, `.json`, vendor blobs, captures). It doesn't belong
anywhere else:

| Layer | Right job | Why raw data isn't here |
|---|---|---|
| ParquetBackend (runs) | measured outcomes / yield / SPC | would bloat the analysis layer |
| ChannelStore | live streaming numerics | not a durable archive — segments age out |
| EventStore | ordered metadata timeline | opaque JSON payload, not performant for find/read of files |
| **Raw-data store** (missing) | **durable** put + access for heavy artifacts | ✓ the right home |

Naming: the layer's *purpose* is "raw data persistence and access"; the
implementation is a **FileStore** (a first-class, session-scoped peer of
ChannelStore). The MVP version is just `_ref` formalized as a real store
with a live lifecycle — the two scattered dirs unify, blobs get the
claim-check numerics already get, EventStore and ParquetBackend keep
their roles. The full taxonomy:

| Layer | Holds | Claim | Index / search |
|---|---|---|---|
| EventStore | immutable timeline; small metadata + claims | — | envelope columns only |
| ChannelStore | live numerics | `channel://` | none today |
| **Raw-data store (FileStore)** | durable heavy artifacts | `file://` | none in MVP; deferred |
| ParquetBackend | denormalized analysis rows | — | DuckDB `_index.duckdb` (run-scoped) |

Against the assignment: the raw-data store serves **Diagnosis** (the
captured trace you reach for at root-cause) and **Evidence** (the
artifact in the traceability trail — the customer's `.tdms`, the failure
screenshot). Both demand **durability** and **claim-check linkage from
the evidence record** — but neither requires attribute query at MVP.
"Findable from the run/event that referenced it" is just URL resolution,
which the existing artifact viewer + ref endpoint already serve.

`docs/concepts/data/three-stores.md` updates **when this lands**, not
before.

### MVP scope (initial release) — store + reference, no attribute search

**In:**
- **Durable put** — `put(key, value, attrs) -> file://…`; reuse
  `save_ref_to_dir`'s type dispatch; available **live**, session-scoped;
  unify the two `_ref` dirs.
- **Streaming sink** — `open(key, format) -> sink; write(chunk); close()`
  for video / large captures; emits the existing
  `FileStarted`→`StreamFrameIndex`→`FileEnded` events; final
  `file://` claim in the closing event.
- **`file://` claim-check from events and from materialized run
  archives** — fixes the image-drop (`InstrumentRead` blobs and
  `observe()` blobs route through the raw-data store instead of
  `repr()`).
- **Attributes captured at put time as self-description** — intrinsic
  facts (mime, dtype, dimensions, size) ride with the artifact so it can
  travel + the viewer can render it. **Not indexed** in MVP.
- **Surface via the existing API** — `artifact_viewer.py` + `_mime`
  already render image/video/PDF; `_serialize_ref` returns downloadable
  Responses for unknown types. Free.

**Out (long-term, deferred):**
- **Per-store attribute index** for raw data — "files where width >
  1024," "captures where max > X." The TDMS-properties / DIAdem move.
  Same shape would apply to a channel-attribute index. Both require
  promoting attributes into a typed, prunable column store; both are
  greenfield index work. Tracked, not in v1.
- **Search by raw time-series values** — not a goal, ever (not an
  industry pattern).

### Backend

Local FS today (what `_ref` uses). Pluggable backends (local / S3 / NFS)
later → **`fsspec`** is the "uniform put/open/stream" answer (the
MLflow-style move). Serve/stream-*out* already works: FastAPI + `_mime`
serve image/video/PDF with HTTP range requests (`artifact_viewer.py`).

---

## The user surface — what test authors write

The whole consumer-facing API is two verbs. Test authors **never call ChannelStore or FileStore directly** — those are platform infrastructure reached transitively through the verbs.

| Verb | Who writes it | What it does |
|---|---|---|
| `observe(name, value)` | test author | vector snapshot — stamps `out_<name>`. Scalar → inline; non-scalar → `file://…` via FileStore. **Never writes to ChannelStore.** |
| `verify(name, value, limit=None)` | test author | measurement row + the same value dispatch. With limit → judged; without → DONE; non-scalar → DONE with `value=NULL` and `out_<name>` claim. |
| `observer.read(...)` | driver author (inside instrument code) | appends to ChannelStore + emits `InstrumentRead`. **The only path that writes ChannelStore.** |
| `filestore.stream(name, format)` | test or driver | opens a streaming sink (`Stream*` events) → one file written incrementally |

So channels appear because instruments use the observer; files appear because observe/verify routed a non-scalar there. The stores are infrastructure; the user surface is verbs.

### Value-type dispatch (the only routing the system does)

For `observe` / `verify`:

| Value type | Destination | What `out_<name>` carries |
|---|---|---|
| scalar (`float` / `int` / `bool` / `str`) | inline in the vector | the scalar itself |
| anything else (`Waveform` / `ndarray` / `Path` / `bytes` / image / model) | FileStore (one artifact per call) | `file://…` URI |

`observe` / `verify` **never** route to ChannelStore. Continuous numeric time-series belong to instruments (which reach ChannelStore via `observer.read`); discrete artifacts from test authors go to FileStore. The same `ndarray` could fit either use case, but the verb the author called encodes the choice — there's no type-sniffing decision to make. This is a deliberate departure from today's `classify_value` (`ref.py:19-39`), which sends `numeric_array` → ChannelStore for any caller. See build item 6.

### Two notions of "streaming," same word

| Sense | Where it lives | Shape |
|---|---|---|
| Row-by-row live delivery on every write | ChannelStore — inherent | typed rows with timestamps |
| Incremental writes to a single file | FileStore — explicit sink API | bytes to one file |

ChannelStore is streaming by nature — every `write()` triggers `_notify` + `_flight_push` (`store.py:324,327`); subscribers see each row as it lands. No "open a stream" — just write. FileStore needs an explicit sink for the "bytes → one file" shape — the `Stream*` events at `events.py:616-628` are the live-tracking vocabulary; no writer backs them today. Used for video and continuous captures that produce bytes incrementally; the result is one artifact at the end.

You never put video frames in ChannelStore (no row schema fits raw frames; per-row notifications would saturate). You never put 100kHz voltage samples through the FileStore sink (you'd lose typed-row queryability and per-sample timestamps). Same word, two shapes, no overlap.

### Serialization — the type→file dispatch

FileStore owns a serialization registry because `observe(name, value)` hands it a Python object, not a file. Three tiers, in lookup order:

1. **Built-in handlers** (the existing `save_ref_to_dir` types, promoted from if/elif to a real registry):

   | Value type | Format | Source |
   |---|---|---|
   | `Path` | copy, suffix preserved | shutil-copy |
   | `Waveform` | `.npz` with Y/t0/dt + attrs as scalar entries | `_row_helpers.py:511-517` |
   | `bytes` / `bytearray` | `.bin` | write_bytes |
   | Pydantic `BaseModel` | `.json` | `model.model_dump_json()` |
   | `numpy.ndarray` | `.npy` | `np.save` |
   | `PIL.Image.Image` *(new)* | `.png` | opportunistic on import |
   | `pandas.DataFrame` *(new)* | `.parquet` | opportunistic on import |

2. **Project / driver registration** — `filestore.register_serializer(VendorType, save_fn)` for SDK types the project doesn't control.

3. **Protocol** — objects implementing `litmus_serialize(dest_dir, stem) -> Path` save themselves. For user classes that know their own format.

4. **Pickle fallback** with a `RuntimeWarning` that names the type — the safety net signals "add a handler" without silently producing pickle-only artifacts.

Streaming captures (video, continuous DAQ) bypass this dispatch entirely — they use the FileStore sink which writes bytes directly in a chosen format.

---

## When to use what — decision matrix

The synthesis of the verb table, the dispatch table, and the data-type taxonomy. Pick the row that matches what you're trying to do; everything else follows.

| You want to… | Verb | Value you pass | Stored in | Event | Parquet row |
|---|---|---|---|---|---|
| Judge a scalar against limits | `verify(name, v, limit=L)` | `float` / `int` / `bool` | event payload | `Measurement` | judged row (`value=v`, `outcome=PASSED/FAILED`) |
| Record a scalar with no judgment (characterization scalar) | `verify(name, v)` no limit | `float` / `int` / `bool` / `str` | event payload | `Measurement` | DONE row (`value=v`, `outcome=DONE`) |
| Stamp contextual scalar on the vector (UUT temp, supply, operator) | `observe(name, v)` | `float` / `int` / `bool` / `str` | `out_<name>` inline | `Observation` | only if vector has no `verify` → DONE row; else rides along |
| Capture a discrete artifact (waveform, image, file, vendor blob) | `observe(name, v)` | `Waveform` / `ndarray` / `Path` / `bytes` / image / model | FileStore (`file://…`) | `Observation` w/ `file://` claim | same auto-promotion rule |
| Force a first-class row for an artifact (alongside derived verifies) | `verify(name, v)` no limit | non-scalar | FileStore | `Measurement` w/ `file://` claim | explicit DONE row (`value=NULL`, `outcome=DONE`) |
| Judge a derived stat computed from a captured artifact | `verify(name, derived_scalar, limit=L)` | `float` (computed from the artifact) | event payload | `Measurement` | judged row; sees source URI via `out_*` on the same vector |
| Stream live numerics from an instrument | `observer.read(...)` (inside driver code) | scalar or array | ChannelStore row | `InstrumentRead` | no (channel data is not a measurement) |
| Stream bytes incrementally to one file (video, large continuous capture) | `with filestore.stream(name, format) as sink:` | bytes via `sink.write(chunk)` | FileStore (one file written incrementally) | `FileStarted` / `StreamFrameIndex` ×N / `FileEnded` | no (artifact is not a measurement) |

### Quick decision tree

```
Do you want to judge a value against a limit?
├── yes → verify(name, scalar, limit=L)
└── no
    │
    Do you need a measurement row in parquet?
    ├── yes → verify(name, value)   (no limit; non-scalar OK)
    └── no  → observe(name, value)  (vector stamp only)

What kind of value?
├── scalar  → lands inline; out_<name> = value
├── array / Path / bytes / image / model → lands in FileStore; out_<name> = file://…
├── live continuous numerics from an instrument → driver uses observer.read() — channel store; you don't touch this directly
└── live continuous bytes to one file → filestore.stream(...) — explicit sink
```

### What test authors actually write (the 95% case)

```python
# scalar judgment (the most common thing in a test)
verify("vout", dmm.measure_voltage(), Limit(low=3.2, high=3.4))

# scalar characterization (no spec yet, just recording)
verify("ambient_temp", thermometer.read())

# vector context (lands on every measurement row's out_*)
observe("operator_id",    "ALICE")
observe("supply_voltage", psu.measure_voltage())

# discrete artifact captures (anything non-scalar)
observe("scope.ch1.capture", scope.acquire())              # → file://… (Waveform)
observe("front_panel_photo", camera.snap())                # → file://… (Image)
observe("nicom_dump",        Path("uut.tdms"))             # → file://… (Path copy)

# derived stats from a captured artifact (judged scalars; share out_ source)
wf = scope.acquire()
observe("scope.ch1.capture", wf)
verify("overshoot", overshoot(wf), Limit(low=0, high=0.5))
verify("max",       wf.max(),       Limit(low=3.2, high=3.4))

# continuous channels happen automatically when the test uses an
# instrument that internally calls observer.read() — author does not
# call ChannelStore directly

# streaming captures (video / DAQ-to-file) — opt-in API
with filestore.stream("uut_video", format="mp4") as sink:
    camera.stream_to(sink)
```

### What test authors NEVER write

- `ChannelStore.write(...)` — channels are reached via instrument drivers (which use `observer.read` internally), not by user code.
- `FileStore.put(...)` directly — files are reached via `observe`/`verify` with a non-scalar value, or via `filestore.stream(...)` for incremental bytes.
- `EventLog.emit(...)` directly — events are emitted by the verbs and lifecycle machinery, not by user code.
- A serializer call for `.npz` / `.npy` / `.png` — pass the Python object (`Waveform`, `ndarray`, `Image`); FileStore picks the format via the registry.

---

## Manifestation rules — observations, verifies, and rows

Where data is **stored** (the dispatch above) is separate from how it **manifests** as parquet rows at materialization. The two are orthogonal; the manifestation rules are a small fixed policy.

### Vector grain

- `out_<name>` lives on the **vector**, not the row.
- At materialization, every measurement row in a vector denormalizes the full `out_*` map onto itself. Multiple rows in one vector share the same `out_*` columns.
- **Last-write-wins** for repeated stamps of the same name within a vector. `out_<name>` is a snapshot, not a history.

### Type stability per name (the kind-registry move)

`out_<name>` must be type-stable across vectors and runs — otherwise the parquet column has two types and the materializer must coerce or refuse. Same shape as ChannelStore's `_registry.json` (`store.py:677-686`): first observation of a name registers the kind (scalar-typed column, `file://` URI column, etc.); subsequent observations must match. Mismatches error at materialization time.

This is a new project-level registry the FileStore lift introduces. Same idea ChannelStore already uses for channel descriptors.

### Row emission policy (the auto-promotion rule)

For each vector, at materialization:

| Vector contained | Row emission |
|---|---|
| **≥1 `verify`** | the verify rows are it. Observations ride along as `out_*` columns on every row via denormalization. **No DONE row per observation.** |
| **0 `verify`, ≥1 `observe`** | each observation **promotes to a DONE row**: `name = observation_name`, `value = NULL`, `outcome = DONE`, full vector `out_*` denormalized. |
| **0 of either** | no row. Empty vector. |

The decision is **materialization-time**, not eager. Events stream as they happen — observation events are observation events, measurement events are measurement events. The materializer reads the per-vector tally and decides whether observations need their own rows to be visible.

### Explicit override (escape hatch)

If an author wants a DONE row alongside verify rows (e.g., wants the capture itself to be a first-class measurement row, not just an `out_` column on the derived rows), call `verify` on the non-scalar:

```python
verify("scope.ch1.capture", wf)                       # explicit DONE row
verify("overshoot", overshoot(wf), Limit(low=0, high=0.5))
```

Auto-promote covers the common case (author didn't think about it); explicit `verify` covers the rare case (author has a reason).

### Cost worth naming

When a test evolves from pure characterization → mixed (author adds the first `verify`), the DONE rows from observations **disappear in new runs**. Old runs still have them; new runs don't. The principle is consistent — observations are vector *context*; rows are for *measured* things — but it means cross-version queries by `measurement_name` won't see the captures in v2. The reliable cross-version query is `WHERE out_<name> IS NOT NULL` — ask by *data presence*, not *row name*. Doc it.

---

## Events as the spine

Every meaningful operation emits an event; each carries data inline (when small) or a **claim URI** (when not). Every consumer — live or at rest — reaches data by walking events and following claims.

### What gets emitted, what gets stored where

| Operation | Event emitted | Data lands in | Event payload |
|---|---|---|---|
| `observe(name, scalar)` | `Observation` *(needs adding — silent today)* | event itself | `name`, `value` inline |
| `observe(name, blob)` | `Observation` *(needs adding)* | FileStore | `name`, `file://…` claim, mime/dtype attrs |
| `verify(name, scalar, limit)` | `Measurement` | event itself | `name`, `value`, `limit`, `outcome` |
| `verify(name, non_scalar)` | `Measurement` | FileStore | `name`, `value=NULL`, `outcome=DONE`, `file://…` claim |
| `observer.read(scalar)` *(driver)* | `InstrumentRead` | ChannelStore row | scalar inline |
| `observer.read(array)` *(driver)* | `InstrumentRead` | ChannelStore row | `channel://…` claim + `{length, sample_interval, min, max}` inline (`events.py:543`) |
| `filestore.stream(name, format)` | `FileStarted` / `StreamFrameIndex` ×N / `FileEnded` (`events.py:616-628`) | FileStore (one file, written incrementally) | `file_id` / `format` / `path`, final `file://…` in `FileEnded` |
| Run / step / vector lifecycle | `RunStarted` / `StepStarted` / `VectorStarted` / `VectorEnded` / `StepEnded` / `RunEnded` | event itself | identifiers + timestamps |

### Three lifecycle phases — same events, three roles

**Phase 1 — Live (during the run).** Subscribers (UIs, MCP tools, log tailers) connect to the event stream and react in real time. A channel detail UI receives `InstrumentRead` → reads the `channel://` URI → queries or subscribes to ChannelStore → re-renders the plot. A file detail UI receives `Observation` with a `file://` claim → reads the artifact via the FileStore endpoint. A stream watcher subscribes to `StreamFrameIndex` → reads partial bytes from the still-being-written file. The event tells you **what** and **where**; the store tells you the bytes.

**Phase 2 — Materialization (at `RunEnded`).** The materializer walks the event log for the run and builds the parquet measurement table. Measurement events → rows. Observation events in the same vector → `out_*` columns denormalized onto every measurement row in that vector. The auto-promotion rule decides whether observations also become DONE rows. Claim URIs flow through as-is into the parquet columns. `materialize_channel_refs` does its session-keyed copy-on-prune step when channel retention triggers (separate, retention-driven, not at RunEnded).

**Phase 3 — At rest (post-run).** Two query surfaces, two purposes. **Parquet** is the analytical surface — `SELECT … FROM measurements WHERE …` by name / value / outcome. `out_*` columns hold scalar snapshots or claim URIs. Joins to part / station / spec for SPC. **Event log** stays queryable for replay / audit / "what exactly happened in this vector." Filter by `event_type`, `run_id`, `session_id`, `event_number`. **Following a URI** is the raw-data drill-down — `file://…` resolves via FileStore (artifact viewer renders or serves bytes); `channel://…` resolves via ChannelStore (rows for the session, plot or compute).

### Why events aren't the search surface

The event IPC schema (`event_log.py:31-40`) is typed envelope columns (`event_number` / `event_type` / `session_id` / `received_at`) + a single **opaque `json` string** holding the whole payload. Filtering by envelope is fast (typed columns); filtering by anything inside the JSON (min, max, units, channel_id, limit) is full scan + per-row JSON parse + no statistics + no pruning. This is why per-store attribute indexes (long-term build item 10) exist as a separate surface — events are the spine, but not the searchable index.

### Time sync between continuous artifacts and point measurements

A **measurement** is a point event — one `received_at` timestamp, fired the moment `verify` was called. A **vector / step / run** is a range — bracketed by `VectorStarted`/`VectorEnded`, `StepStarted`/`StepEnded`, `RunStarted`/`RunEnded`. A **continuous artifact** (video, audio, long DAQ file) is also a range — bracketed by `FileStarted`/`FileEnded`.

Given a continuous artifact and any of the above, the sync mechanic is the same — subtract on the shared event clock:

| You want | Compute |
|---|---|
| Video moment for a single measurement | `measurement.received_at − FileStarted.received_at` → one offset (seconds) |
| Video segment for a vector | `(VectorStarted − FileStarted, VectorEnded − FileStarted)` → start/end offsets |
| Video segment for a step | `(StepStarted − FileStarted, StepEnded − FileStarted)` |
| Channel samples concurrent with a measurement | `ChannelStore.query(channel_id, since=measurement.received_at − ε, until=measurement.received_at + ε)` |
| Channel samples covering a vector | same query, keyed on the vector's start/end |

The viewer seeks to a point (`#t=12.3`) for a measurement, or plays a range (`#t=12.3,18.7` per the [W3C Media Fragment URI](https://www.w3.org/TR/media-frags/) spec) for a vector / step / run. Both are honored natively by HTML5 `<video>`. The artifact viewer already serves with HTTP range requests, so seeking works for partial files too (still being written during a live run).

This isn't video-specific — it's how any **continuous-artifact ↔ time-bounded-thing** sync works:

| Continuous artifact | Sync target | How |
|---|---|---|
| Video | measurement (point) / vector / step / run (range) | clock subtract → seconds offset |
| Audio | same | same |
| Long DAQ `.tdms` | same — file has its own time axis | clock subtract → file time |
| Live channel data | same | range query keyed by `received_at` |

The platform commits to one clock (the event log's `received_at`); everything else is subtraction. No per-artifact sync protocol; no synchronized-clock machinery beyond "all events written to the same log."

**Frame-accurate sync** (deferred). For cases where ±33ms isn't tight enough (high-speed diagnostic capture, slow-mo failure recordings), `StreamFrameIndex` can carry `pts_seconds` per frame. A measurement timestamp joins to the nearest `StreamFrameIndex` by `received_at` proximity → exact frame index. Same mechanism, higher granularity. Not MVP.

---

## The federation model: ownership, retention, portability (ref-vs-copy resolution, 2026-06-09)

A design session — grounded in lakehouse / TSDB / ML-platform prior art — resolved how
cross-store references, retention, and archival actually work, and concluded the eager
**copy** (`materialize_channel_refs`) is the wrong mechanism. This supersedes the
copy-on-prune description above.

### The stores are a federation, not a relational database

The four stores reference each other by URI (`channel://`, `file://`) but they are **not
related tables in one engine**: separate stores, separate daemons, separate files,
separate retention, no cross-store transaction, no enforced foreign keys. This is
unsettling only against an RDBMS mental model. The correct model is the **data-lake /
metadata-store split**, the dominant pattern wherever heavy data is separated from
metadata:

- **MLflow** — backend store (metadata DB) + artifact store (S3); a run references
  artifacts by URI; the metadata DB neither holds the bytes nor enforces the link.
  One-to-one with events/runs (metadata) ↔ channels/files (heavy data).
- **Iceberg / Delta Lake** — a catalog/manifest + data files in object storage; the
  "table" is a list of **relative** file paths; no engine prevents deleting a referenced
  file — "missing/orphan file" is a *handled* state, not an impossibility.

So "they know about each other but aren't one database" is the defining property of a
lakehouse, not a smell.

### Two coordination seams (the only places the stores touch)

Both are plain API calls — never a shared transaction, never reaching into another
store's files:

1. **Read-resolution** — a consumer follows a `channel://` / `file://` URI by calling the
   *owning* store's API (`channel_query_client(...).query(...)`), never by reading files.
   (Store boundary = API boundary.)
2. **Retention-reachability** — before pruning, a store asks "who references this?" via
   `run_store.find_channel_refs(...)`. Referenced → kept; unreferenced → pruned.

### Reference-aware retention — don't copy, pin the referenced

**Decision: do not copy referenced data. Keep it in place; prune only the unreferenced.**
The lakehouse `VACUUM` model: Delta removes files "no longer referenced *and* older than
the threshold" (referenced files are never vacuumed); Iceberg removes files "not
referenced by any current snapshot" (data lives while reachable).

The reachability query **already exists** — `materialize` calls
`run_store.find_channel_refs(session_shorts)`. Reference-aware retention is the *same
query, opposite action*: instead of *"copy what it returns"*, *"prune everything **except**
what it returns."* No copy, no cross-store glob, **no redundant large-file duplication**.
Channel data **no run references** ages out normally (the TSDB pattern: raw is transient;
TimescaleDB/Influx age-out + downsample + tier).

**`materialize`-on-prune is a lateral no-op and is dropped.** It copies a slice from the
channel store into the *file* store and rewrites `channel://` → `file://` — changing
*which* store owns the bytes without making the run self-sufficient (it still points
outward, now at FileStore). It pays an export's duplication cost with none of an export's
portability benefit. Reference-aware retention replaces it.

### Lifecycle-dependent ownership

Ownership changes with lifecycle stage, exactly as in MLflow/lakehouses:

| Stage | Who owns the bytes | Precedent |
|---|---|---|
| Operating (bench / central server) | the **store** owns its data; runs hold federation pointers | lakehouse table ↔ data files; MLflow run ↔ artifacts |
| Archiving (hand-off / backup / audit) | a **sealed bundle** owns a copy of everything | Iceberg clone; MLflow export dir; OpenHTF `result.json` |

A run is **not** expected to be self-sufficient while it lives in the system — it becomes
self-sufficient when it *leaves*, via an explicit seal/export. Self-sufficiency is a
property of the boundary, not the live store.

### Two portable grains — and they are different tools

Because **the data files are the source of truth, every index is a derived cache rebuilt
from them, and every reference is relative** (`file://` is a backend-root-relative key;
`channel://` is channel-id + session — never an absolute path):

- **Coarse — whole `data_dir`:** `cp` / `rsync` / **merge** two dirs; first daemon access
  rebuilds the index from the (possibly unioned) files. Safe because identities are uuid4
  (no collisions) and refs never cross a `data_dir`. The lakehouse superpower (copy the
  table dir, the catalog recomputes) — impossible with an RDBMS. The **backup/relocate**
  tool.
- **Fine — selected runs + what they reference:** the run parquet + *only* the
  `channel://`/`file://` slices it points at (reachability via `find_channel_refs`),
  daemon-free. The **promote / seal / hand-off** tool. Matches MLflow export (run + its
  artifacts) and OpenHTF `result.json` (record + attachments) — "record + what it
  references," never "everything."

**Gotcha (coarse grain):** the *data* relocates cleanly; the *daemon state files*
(`_*.json` / `_*_pid` / `_*.lock` / `_ready` / port files) are machine-specific and must
be cleared on copy/merge so daemons cleanly respawn + rebuild — a tooling job
(`litmus import` / `merge` / `relocate`).

### The integrity contract — tooling, not engine constraints

There is no constraint engine, so **the tooling is the integrity layer**: reference-aware
retention, emit-ref-only-after-durable (atomic publish), import/merge/relocate.

- **Through the tooling → the system's responsibility.** Referenced data is never pruned;
  refs are never emitted before bytes are durable; relocation rebuilds cleanly.
- **Outside the tooling (manual file surgery) → the user's responsibility.** Nothing can
  stop a manual `rm` of referenced data or a bad hand-merge; the user owns that outcome.
  Verbatim the lakehouse contract ("don't manually delete data files; use `VACUUM`").
- **The system's guarantee in return: fail loud, never silent.** A dangling reference
  resolves to a clean "not found" and is **surfaced** (flagged in the index, shown to the
  operator — #263), never silently corrupted. The same no-hide-data rule applied
  everywhere: a missing reference is an operator-visible signal.

### `litmus data promote` is the first cross-federation tool — and today it's broken

`litmus data promote` (`cli.py:2511`) copies **only** `runs/runs/*.parquet` to the global
store and suggests `rm -rf {src_data}` afterward. Under the federation model that dangles
every `channel://`/`file://` ref the promoted parquet holds **and** discards the events
spine (the *source* the parquet is merely a derived view of). It promotes one store, not
the run's reachable set.

Fix: promote the **fine grain** — runs + their referenced channel slices + referenced
files (reachability via `find_channel_refs`). **Default = runs + references**; the parquet
already denormalizes serial/spec/station/cal/operator/conditions per row, so it's
audit-ish without the raw timeline. **`--with-events`** adds the session event trail for
compliance-grade archives. Unreferenced channels are *not* carried (transient monitoring —
what retention prunes). Promote and reference-aware retention share the same reachability
machinery.

### Work items

1. **Reference-aware retention** — flip `find_channel_refs` from copy-what's-referenced to
   prune-all-but-referenced; delete `materialize_channel_refs`'s copy + the cross-store
   channel glob. Unreferenced channel data ages out (configurable; TSDB default).
2. **Run seal/export** — explicit op producing a self-contained, daemon-free bundle
   (manifest + referenced channel slices + files; `--with-events` optional). OpenHTF
   output-callback / MLflow-export shape. Net-new.
3. **Fix `litmus data promote`** — carry runs + references (reachability-scoped), not just
   `runs/`; never suggest `rm -rf` the source while refs are unresolved. Shares (2)'s
   machinery.
4. **Dangling-reference resilience** (#263) — the safety net for manual surgery: a missing
   ref reads as a clean, surfaced "not found," never silent corruption.
5. **Relocation tooling** — `litmus import` / `merge` clears stale daemon state + triggers
   rebuild (vs. relying on the incremental scan).

### Prior art

- Reference-aware GC: [Delta VACUUM](https://docs.delta.io/latest/delta-utility.html),
  [Iceberg VACUUM TABLE](https://www.dremio.com/blog/apache-iceberg-table-storage-management-with-dremios-vacuum-table/)
- Time-series age-out + downsample + tier:
  [TimescaleDB downsampling](https://www.tigerdata.com/blog/how-to-proactively-manage-long-term-data-storage-with-downsampling)
- Self-contained export: [OpenHTF output callbacks](https://www.openhtf.com/output-callbacks),
  [mlflow-export-import](https://github.com/mlflow/mlflow-export-import)
- Metadata-store + artifact-store federation:
  [MLflow Artifact Stores](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/)

---

## Build items

Each MVP lift names the **symptom** in current source it fixes. Order is conceptual, not strictly sequential — many are independent.

### MVP (initial release) — raw-data layer + API consistency

**Stores**

1. **Stand up the raw-data store (FileStore)** as a first-class, session-scoped peer of ChannelStore: durable `put(key, value, attrs) -> file://…`, live lifecycle, `file://` URI, attributes captured at put as self-description (mime / dtype / dimensions / size). Unify the two existing `_ref` dirs (`events/{session_id}_ref` + `runs/{stem}_ref` — `event_log.py:247`, `parquet.py:367`).
2. **Streaming sink** behind the existing `File*` events (`events.py:616-628`) — `open(key, format) -> sink; write(chunk); close()` for video and large continuous captures. Final `file://` claim in `FileEnded`.

**API consistency (fix the four asymmetries)**

3. **Blob → `file://` claim-check.** *Symptom:* `InstrumentRead._serialize_with_claim_check` (`events.py:543`) claim-checks arrays via `EventLog.save_ref` but for blobs falls through to `data["value"] = repr(v)` — the bytes are dropped. `Context.observe()` (`harness.py:190-208`) stashes blobs in-memory until RunEnded. *Fix:* both paths route blobs through FileStore and carry a `file://` claim. **This is the image-drop fix.**
4. **`observe()` emits the claim event** the way `observer.read` does. *Symptom:* `Context.observe()` writes to a channel for arrays (and stashes blobs) but emits **no event** — manually-observed captures are untraceable, invisible to live subscribers, missing from the timeline. *Fix:* emit `Observation` per call (claim URI for non-scalars; value inline for scalars).
5. **`observer.read` stamps the vector's `out_*`.** *Symptom:* scalar instrument readings link to verify rows only through the event log today, not through row columns. Breaks the polymorphic `observe`/`verify` symmetry. *Fix:* every channel/file claim emitted in the vector's scope stamps `out_<name>` — same path `observe` will use.

**Dispatch policy**

6. **`observe` / `verify` route non-scalars to FileStore, not ChannelStore.** *Symptom:* `classify_value` (`ref.py:19-39`) currently sends `numeric_array` → ChannelStore for any caller. The two-verb model says ChannelStore is reached only via `observer.read`; discrete captures from test authors go to FileStore. *Fix:* the dispatch from `observe`/`verify` callers becomes binary (scalar inline / non-scalar → FileStore); `observer.read`'s path to ChannelStore is unchanged.

**Materialization**

7. **Auto-promotion rule in the materializer.** New per-vector behavior: `≥1 verify` → verify rows only (observations ride along as `out_*`); `0 verify, ≥1 observe` → promote each observation to a DONE row (`value=NULL`, `outcome=DONE`, full vector `out_*` denormalized). *Without this, pure-characterization runs produce no parquet rows even though observations were recorded.*
8. **Type-stable `out_<name>` registry.** Mirror ChannelStore's `_registry.json`: first observation of a name registers the column kind (scalar-typed vs `file://` URI); subsequent observations of that name must match. Mismatches error at materialization. *Without this, the same name observed as a float in one vector and a Path in another creates a parquet column with two types.*

**Serialization**

9. **Promote `save_ref_to_dir` from if/elif to a registry.** Built-in handlers for the existing types (`Path`, `Waveform`, `bytes`, `BaseModel`, `ndarray`, fallback `pickle` — `_row_helpers.py:484-545`) + opportunistic `PIL.Image.Image` → PNG and `pandas.DataFrame` → Parquet. Expose `filestore.register_serializer(type, fn)` for vendor SDK types the project doesn't own. Define a `litmus_serialize(dest_dir, stem) -> Path` protocol for objects that know their own format. Pickle fallback emits a `RuntimeWarning` naming the type — visible gap, not silent unusable artifact.

**That's a shippable v1.** Every captured artifact is durably stored; every capture leaves an event with a `file://` claim; the API is two verbs with consistent dispatch; parquet rows manifest by a single fixed policy; the existing `artifact_viewer` + ref endpoint already surface the artifacts. No attribute query yet — findability at MVP is **URL resolution from the run/event that referenced the artifact**, which is exactly what evidence + diagnosis need.

### Long-term — findability by attribute, live plot, perf

10. **Per-store attribute indexes.** Promote captured attributes (file intrinsic: width/height/dtype; domain: min/max from `InstrumentRead`; future ChannelStore stats) into typed, prunable indexes so questions like *"files where width > 1024"* or *"captures where max > X"* don't require open-every-file or scan-every-event. Channels and files each own their own index; `run_id` is an optional join, not the scope. The TDMS-properties / DIAdem move.
11. **Live waveform plot.** Subscribe the channel detail page (`ui/pages/channels/detail.py`) to the event stream; redraw on each new `InstrumentRead`. The list page already live-updates via event subscriptions; copy that pattern. (Today the detail's only timer is a one-shot canvas resize — no live path.)
12. **Perf: byte-aware flush + end-to-end Flight bench.** `BufferedIPCWriter` flushes by **row count**, fine for scalars but dangerous for dense arrays (N full waveforms buffered per flush). The existing bench (`test_data/test_perf.py`) measures local writes only; nothing measures the end-to-end Flight streaming path that the live plot will depend on.

### Search by raw time-series values

Not a goal, ever. Not an industry pattern. Search by **derived attributes/stats** is the goal, and it's deferred to (10).

## Corollary (corrected): findability tiers

| Tier | What it needs | When |
|---|---|---|
| **From a run / event you already have** ("show me this run's failure capture") | URL resolution of the `file://` / `channel://` claim → existing artifact viewer / ref endpoint | **MVP** |
| **Across runs by analytical outcome** ("yield by station," "failures by Pareto bucket") | Run parquet (already exists) | **today** |
| **Across raw artifacts by attribute** ("files where width > 1024," "captures where max > X") | Per-store attribute indexes (greenfield) | **long-term (5)** |

Earlier framing — *"InstrumentRead records min/max, so events are the
searchable index, no new index needed"* — was **wrong**. Stats are present
in events but in an opaque `json` column → not performantly queryable;
the only typed index (run parquet) is run-scoped, missing standalone
data. Tier-3 findability is real work, intentionally deferred past MVP.

---

## Convergence with prior art (post-hoc audit)

**Process note up front — this isn't a derivation map.** Most of the systems listed below were *not consulted during the design*. We worked from "what does T&M data need to do" and arrived at shapes that — it turns out — overlap heavily with what other systems independently landed on for similar reasons. This section is a post-hoc convergence audit, not a list of sources we drew from.

The one explicit input that *was* referenced during design is **ATML / IEEE 1671** — terminology (record_type, measurement, session / run / step / vector hierarchy, traceability columns on every row). Other T&M-native systems (TDMS, OpenHTF, ISO 17025) are convergent precedents, not consulted inputs.

The audit is useful anyway for three reasons: it gives readers from other ecosystems a way to orient ("oh, that's like X"); it gives us defensible answers when someone asks "why did you do it this way" (because that's also how other systems solve the same problem); and it surfaces — in the **Editorial moves** subsection below — the small set of places where our design *doesn't* converge with established patterns, which is where extra scrutiny is warranted.

### Software-architecture canon (zero novelty — textbook)

| Pattern | Precedent |
|---|---|
| **Claim-check** (events carry URI, heavy data in stores) | EIP (Hohpe & Woolf, 2003), Azure Architecture Center, Spring Integration |
| **Event sourcing** (immutable append-only timeline as source of truth) | Fowler (~2005), Greg Young's CQRS, EventStoreDB, Kafka log-as-database |
| **CQRS + materialized views** (events = write model; parquet = read model) | Young (~2010); Kafka + ksqlDB / Materialize; Spark Structured Streaming |
| **Three lifecycle phases (live / materialization / at-rest)** | CQRS write side + projection + query side; Elasticsearch refresh + commit; HTAP databases |
| **Star-schema denormalization** (vector context onto every row) | Kimball data warehousing; read-optimization 101 |
| **Single clock + offset arithmetic for sync** | OpenTelemetry trace timestamps, Kafka offsets, SMPTE timecode, video editors |
| **Schema-on-write / type stability per column** | Avro evolution rules, Iceberg / Delta enforcement, InfluxDB field-type immutability |
| **Type-dispatch serialization registry with fallback** | Python `functools.singledispatch`, `pickle.__reduce__`, `json.JSONEncoder.default`, MLflow `pyfunc.flavors` |
| **Layered API audiences** (test author / driver author / platform) | OTel SDK (end-user / SDK / collector), pytest (assert / fixture / hook / plugin) |

### ML experiment-tracking lineage (heavy reuse — the most direct analog, with limits)

| Pattern | MLflow analog | Where the analog stops |
|---|---|---|
| Backend store + artifact store split | **MLflow** — exact analog (metadata DB + S3-style artifact store) | — |
| Artifact URI from a run/event | MLflow `runs:/<run_id>/path`; W&B Artifacts; Neptune; Aim | — |
| Streaming / chunked artifact upload | MLflow `log_artifact_stream`; S3 multipart upload | We extend with `Stream*` events for live read-along during the write |
| Run + context + outputs | MLflow `log_param` (input) + `log_metric` (output scalar) + `log_artifact` (file) | We collapse to two polymorphic verbs dispatching by value type — **and** our entities are denormalized onto measurement rows, not flat siblings under a run (see below) |
| Pickle fallback with warning | MLflow `pyfunc` autolog fallback | — |
| Reproducibility via immutable artifacts + metadata | MLflow, DVC, W&B, Neptune | — |

**Where MLflow stops being the analog.** Two distinct gaps, both worth naming.

*Gap 1 — the T&M centrality of raw data.* MLflow's positioning treats artifacts as *supporting evidence* for the primary structured surface (params + metrics) — browse-and-download UI, not searchable by content, not part of the analytical query surface. That **matches our MVP scope exactly** (raw data referenced, not searched). But MLflow doesn't carry the T&M reality that the raw capture (`.tdms`, scope waveform, camera image) is often *the actual measurement event itself*, and scalars are summaries of it — raw data isn't downstream of measurement, it often *is* the measurement. Two places we go further than MLflow as a result:

1. **Non-scalar observation is a direct verb call** — `observe(name, wf)` hands a Python object; FileStore serializes via the registry. MLflow requires the author to serialize first and `log_artifact(path)`.
2. **Auto-promotion to parquet rows** — pure-characterization runs (no `verify`, only `observe`) produce DONE rows in our analytical view. MLflow runs with only `log_artifact` calls have an empty metrics surface.

*Gap 2 — the relational shape of the analytic surface.* MLflow's entities (params, metrics, tags, artifacts) are **flat siblings under a run**. They share `run_id` as grouping but have no row-level relationship to each other — a metric does not reference a param or an artifact; an artifact does not reference a metric. To ask "which params and artifacts correspond to metric `loss=0.2`?" you join via `run_id` and get *all* params and *all* artifacts on that run.

Our analytic shape is opposite: **everything denormalizes onto the measurement row**. A `verify` row carries its own `value` / `outcome` / `limit_*`, the vector's `in_*` (inputs from `configure`), the vector's `out_*` (observations including artifact URIs), and the session/run context (UUT serial, station, spec) — one query, no joins. "Which inputs and which captured artifact correspond to this overshoot row?" is answered by the row's own columns.

This is a real architectural difference, driven by data-shape needs:

- **MLflow's grain is the run** (few runs, each with many params/metrics/artifacts; analytics compare across runs). Run-as-grouping makes sense.
- **Litmus's grain is the measurement row** (many runs/units, each producing many measurements; analytics are Cpk / Pareto / yield over measurements). Row-as-grouping is what those queries need.

So our shape **converges with** MLflow on the **storage split** (lean metadata + heavy artifact store + claim URI between them), and **converges with** data-warehousing / star-schema (Kimball-school) on the **analytic shape** (denormalized fact rows with measure + context columns). Neither was consulted during the design — the convergence is post-hoc. ATML / OpenHTF converge on the **measurement-as-grain** choice too; ATML in particular *was* referenced during design for terminology and the session / run / step / vector hierarchy. The honest post-hoc map:

| Shape we ended up with | Convergent with | Actually consulted? |
|---|---|---|
| Storage split — lean metadata + heavy artifact store + claim URI | MLflow (and W&B, Neptune, Aim) | no — convergence |
| Analytic shape — denormalized fact rows with measure + context columns | Kimball / star-schema data warehousing | no — convergence |
| Measurement-as-grain — row-per-measurement, not row-per-run | ATML / OpenHTF | **yes — ATML** |
| Session / run / step / vector hierarchy | ATML | **yes — ATML** |
| Two-verb polymorphic dispatch + auto-promotion rule | (no clean precedent — editorial; see below) | (own) |

### Time-series + streaming infrastructure (heavy reuse)

| Pattern | Precedent |
|---|---|
| Append-only time-series store with row-level live delivery | InfluxDB, TimescaleDB, Prometheus, kdb+ |
| Apache Arrow IPC + Flight RPC | Apache Arrow project — used by Dremio, InfluxDB IOx, BigQuery, Snowflake, Ray, Dask |
| SCALAR_SCHEMA vs ARRAY_SCHEMA per row | OpenTSDB raw-vs-aggregated; InfluxDB downsampling continuous queries |
| Session / segment file rotation | Kafka log segments; LSM-tree SSTables; log4j rotating appenders |
| Per-row notify subscriptions | Kafka consumer streams; Redis pub/sub; PostgreSQL `LISTEN/NOTIFY` |

### T&M-native systems (the precedents that matter most to our audience)

| Pattern | Precedent |
|---|---|
| File / group / channel + properties at any level | **TDMS** (National Instruments) — directly cited; our `Waveform(t0, dt, Y, attrs)` mirrors TDMS waveform |
| Self-describing scientific data files | HDF5 (1988+), NWB (Neurodata Without Borders), NeXus, Zarr, NetCDF |
| Properties indexed for cross-file search | NI DIAdem (indexes TDMS properties); HDF5 + indexing tools |
| **The four jobs** (disposition / traceability / yield / diagnosis) | ISO/IEC 17025 (lab quality / traceability), SEMI E10 / E58 (equipment metrics), AIAG MSA (Cpk / Gauge R&R), Western Electric / Six Sigma (SPC) — each job traces to an established T&M domain |
| Measurements with limits + outcomes, attachments as side artifacts | **OpenHTF** — `measurements` (with limits) + `phase.attach_from_file()` (artifacts) |
| Calibration cert + traceability spine | ISO/IEC 17025, A2LA accreditation |
| UUT serial + station + operator + spec on every row | MES / shop-floor data; SEMI SECS/GEM; IEEE 1671 ATML test result format |
| Sample-rate-derived time axis (`t0 + dt`) | TDMS waveform encoding; IEEE 1671; MATLAB time-series objects |

### Observability lineage (close cousin, lighter reuse)

| Pattern | Precedent |
|---|---|
| Vector context propagates to child events | OpenTelemetry span attributes ↔ our vector `out_*` denormalized onto measurement rows |
| Spans bracket time ranges; events are points | OTel spans + events ↔ our vector / step / run brackets + measurement events |
| `Stream*` events as progress notifications | OTel span events; distributed-trace waterfall markers |
| Resource attributes / context propagation | OTel `Resource` ↔ our session / run / station / serial context |

### Web standards (direct adoption)

| Pattern | Precedent |
|---|---|
| `#t=12.3` / `#t=12.3,18.7` media fragments | [W3C Media Fragment URI](https://www.w3.org/TR/media-frags/); honored by HTML5 `<video>` |
| HTTP range requests for partial reads | RFC 7233 |
| fsspec for backend abstraction | Pluggable filesystem abstraction (cited in Sources below) |

### URL / claim semantics

| Pattern | Precedent |
|---|---|
| Session-bearing claim URI (`channel://id?session=…`) | S3 versioned URLs (`?versionId=…`); Kafka topic + partition + offset; MLflow `runs:/<run_id>/path` |

---

### Editorial moves (the small, bounded "this is ours")

About 2% of the design is editorial. Worth being deliberate about each.

**1. Auto-promotion rule** (vector with ≥1 `verify` → no DONE rows for observations; vector with 0 `verify` + ≥1 `observe` → each observation promotes to a DONE row).

Precedent comparison:

- **OpenHTF** always promotes — every recorded value is a measurement row regardless of judgment.
- **OpenTelemetry** never promotes — span attributes are pure context.
- **MLflow** never promotes — `log_metric` and `log_artifact` are distinct surfaces.

Our conditional rule serves the use case (characterization-only tests get visible rows; mixed tests don't get noise) but it's the one place a reader from another ecosystem will need to be told "this is the rule." Doc it clearly; watch for "this confused someone" feedback during early adoption.

**2. Polymorphic `observe` / `verify` verbs over scalar + non-scalar.** MLflow and OTel chose separate functions per value type (`log_metric` vs `log_artifact`; `Counter` vs `Histogram`). Our choice to fold all value types under one verb is a Python-flavored polymorphism move — better ergonomics, more invisible dispatch. The serialization registry + warning fallback is what makes this safe.

**3. Verb names `observe` and `verify`.** "Verify" is T&M-native (verify against spec). "Observe" is more astronomy / OTel. Together they're our pair — good words, not borrowed from a specific predecessor.

**4. Session ⊃ run ⊃ step ⊃ vector nesting.** MLflow runs are atomic — no nested concept. OpenHTF has phases inside tests but no explicit session above tests. Our four-level nesting is finer-grained than typical, which is why "channels are session-scoped, runs reference them by session-bearing URI" needs to be spelled out. The mechanism (URI with scope query) is standard; the four levels are editorial.

**5. The "four jobs" framing as a single design rubric.** Each job is individually industry-canon (see T&M-native row above), but threading them through every design decision as a rubric is editorial. Defensible editorial — gives the design a spine — but it's our framing, not borrowed.

### Net

The biggest risk is **not** novelty — it's the opposite: that the design looks generic from a distance until you see it specifically matches T&M domain shape (ATML hierarchy, OpenHTF measurement-with-limits, ISO 17025 traceability). The "four jobs" anchor + the T&M-native convergence is what makes it look like a T&M solution rather than an MLflow knock-off — even though we didn't consult MLflow during the design either.

None of the editorial moves are architectural bets. They're surface choices, recoverable if we change our minds. The infrastructure underneath converges with well-trodden patterns and is individually defensible by precedent (post-hoc), even where those precedents weren't the source.

---

## Sources

**Software-architecture patterns**
- Claim-check — [Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/patterns/claim-check), [Spring Integration](https://github.com/spring-projects/spring-integration/blob/v6.4.1/src/reference/antora/modules/ROOT/pages/claim-check.adoc), [EIP / Software Patterns Lexicon](https://softwarepatternslexicon.com/enterprise-integration-patterns/message-transformation/claim-check/)
- Event sourcing — Martin Fowler, ["Event Sourcing"](https://martinfowler.com/eaaDev/EventSourcing.html) (2005); [EventStoreDB](https://www.eventstore.com/)
- CQRS — Greg Young, ["CQRS Documents"](https://cqrs.files.wordpress.com/2010/11/cqrs_documents.pdf) (2010)
- Kafka log-as-database — [Jay Kreps, "The Log"](https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying)

**ML experiment tracking**
- MLflow backend-store vs artifact-store + multipart upload — [Artifact Stores](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/), [Backend Stores](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/), [`log_artifact`](https://mlflow.org/docs/latest/python_api/mlflow.html#mlflow.log_artifact)
- W&B Artifacts — [docs.wandb.ai/guides/artifacts](https://docs.wandb.ai/guides/artifacts/)

**Time-series + streaming infrastructure**
- Apache Arrow + Flight — [Arrow project](https://arrow.apache.org/), [Arrow Flight](https://arrow.apache.org/docs/format/Flight.html)
- InfluxDB / TimescaleDB / Prometheus / kdb+ — canonical append-only time-series stores

**T&M-native systems**
- TDMS internal structure (file / group / channel + properties; waveform = start + increment + array) — [NI: TDMS File Format Internal Structure](https://www.ni.com/en/support/documentation/supplemental/07/tdms-file-format-internal-structure.html), [npTDMS](https://nptdms.readthedocs.io/en/stable/reading.html)
- OpenHTF (measurements with limits + attachments) — [google/openhtf on GitHub](https://github.com/google/openhtf)
- HDF5 — [hdfgroup.org](https://www.hdfgroup.org/solutions/hdf5/)
- ISO/IEC 17025 — calibration and traceability in T&M labs
- AIAG MSA — measurement system analysis (Cpk, Gauge R&R)
- IEEE 1671 ATML — test result format with serial / station / spec on every record

**Observability**
- OpenTelemetry (span attributes, events, resources) — [opentelemetry.io/docs/concepts/](https://opentelemetry.io/docs/concepts/)

**Web standards**
- W3C Media Fragments URI (`#t=` syntax for video/audio time seeks) — [w3.org/TR/media-frags/](https://www.w3.org/TR/media-frags/)
- HTTP range requests — [RFC 7233](https://datatracker.ietf.org/doc/html/rfc7233)

**Pluggable filesystem abstraction**
- fsspec — [filesystem-spec.readthedocs.io](https://filesystem-spec.readthedocs.io/)
