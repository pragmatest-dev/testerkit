# Channels: the real-stream problem — handoff

**Status:** RESOLVED (2026-06-13) — see "## Resolution (built)" at the bottom.
The 2× target this doc opens with was **superseded**: working through it showed
`stream`, `write`, and `write_many` are one operation, so they were *aligned* onto
a single columnar core rather than made to race each other. The body below is the
original problem statement, kept for context; read the Resolution section for what
was actually built and decided. Internal note — file:line citations and internal
names are fine.

**Companion docs (read these first):**
- `channels-write-scaling.md` — the measured bottleneck analysis + validated
  design (§5) + req6 swap audit. This is the canonical evidence base.
- `data-store-backends.md` — the six-requirement service contract and the
  per-store backend-swap targets (channels → a TSDB; the swap shape matters).
- `data-stores.md` — the four-store architecture, verbs, performance tables.

The active scratch plan is `~/.claude/plans/sleepy-twirling-bentley.md` (agent
scratch, not team-visible — fold its still-relevant parts into a repo doc).

---

## The goal in one sentence

Make `channels.stream` an **actual stream** — a live, point-to-point producer
connection that clients experience as a stream — such that (a) all the write
verbs **scale** across concurrent producers and (b) the implementation is
**swap-clean** to a real server backend (a time-series DB), behind the same
verbs.

A stream must be fast *because* it's a real streaming connection, the way
`files.stream` is a real open file handle — not a buffer that periodically calls
a batch verb.

### The acceptance target (set by the user)

- **`stream` must be ~2× faster than `write_many`** — not tied with it (today it
  ties: 440k vs 470k). 2× is the bar that proves `stream` earns its own verb.
- **The speed buys a behavior nuance, and the *only* acceptable trade is in
  queryability / liveness / latency-of-visibility — NEVER durability, NEVER risk
  of data loss.** Every flushed sample is durably persisted, full stop. A stream
  may be, e.g., *not at-rest-queryable until close*, or *eventually-consistent in
  the index*, or *live-from-now (drop-on-overflow for late subscribers)* — those
  are acceptable, documented nuances. Dropping or risking durably-written data is
  not on the table. (This is the FileStore streaming contract too: bytes hit the
  durable object on every `write`+`flush`; only the *catalog* lands on close.)

### Do this FIRST: prior art on streaming behavior

Before designing, survey how mature streaming/TSDB systems make their *stream*
path faster than their *batch-insert* path, and what nuance they trade for it.
Likely sources: **InfluxDB** (line-protocol ingest vs batch writes; WAL +
deferred TSM compaction), **QuestDB** (ILP over TCP; out-of-order commit lag),
**TimescaleDB** (hypertable inserts; continuous aggregates as deferred views),
**ClickHouse** (async inserts; `MergeTree` parts merged later), **Kafka**
(append-then-index; consumer-lag model), **kdb+ tickerplant** (log append →
independent index/live cursors). The recurring pattern to look for: *durably
append now, make it queryable later* — which is exactly the "speed-for-
visibility, never-for-durability" trade above. Bring back what nuance each
system trades, then pick ours deliberately.

---

## What "a real stream" means (and what we have instead)

**Reference implementation — `files.stream` (`data/files/streaming.py`):** opens
**one** durable handle at construction; `write(chunk)` appends straight to it +
flushes + fans the new bytes to live subscribers ephemerally (`publish_frame`,
not the event log); emits **lifecycle events only** (`FileStarted` at open,
`FileEnded` at close); writes the **catalog/index row only in `close()`**
(S3 multipart completes there — the object-store model *forces* index-on-close).

**What the channel "stream" actually is today (`channels.py` `_ChannelSink`):**
a buffer with a `threading.Timer`. `sink.write(v)` appends to a list under a
lock; on size/interval it flushes the buffer by calling `store.write_many(...)`.
It is a **batch verb with a buffer glued to the front** — not a connection.
Consequence: it inherits the entire `write_many` path (per-sample Pydantic +
dict build, durable append, index feed, push) and benchmarks essentially tied
with `write_many` (440k vs 470k), when it should be the *fastest* verb.

**The consumer side is already a real stream** — `do_get` → `_live_stream`
returns a `flight.GeneratorStream` that yields batches as they arrive
(`server.py:183`), fed by `_relay_batch` → per-subscriber `_SubscriberRing`
(`server.py:101/32`). So the live wire on the *read* side exists and works
(~1ms p99 write→subscriber, `channels-write-scaling.md` §3). **The producer
side is the half that isn't a connection.**

---

## The core problem: the producer path is not a connection (NOT the index)

> **Correction (read this):** an earlier draft of this doc made "delete the
> dual-write" the centerpiece. That was wrong and is retracted. We are **not**
> deleting indexing, and we are **not** undoing the batching work. The index
> stays. The problem is the *producer-to-wire path*, not the index.

**What the index already is, and why it's fine.** Querying is served by
`channel_index` (derived from **closed** segments by `_scan_disk`,
`store.py:1068/1113`) **∪** `live.channel_live` (an in-memory overlay of recent
rows), deduped by `offset` — a sample is in exactly one (overlay until its
segment is scanned, `channel_index` after, `store.py:1345`). The overlay is fed
by the push via columnar batched `ingest_batch` (`store.py:1296–1301`). The
24-hour work (async accumulating pusher + columnar ingest) made this **fast and
non-blocking** (write 21k→143k, write_many →470k). The req6 audit in
`channels-write-scaling.md` already concluded this index design is **swap-clean**
(offset = opaque cursor, at-rest = derived, push = live consumer). **There is no
drift and no swap blocker that "deleting the index" would solve.** Keep it.

**The dual-write harm was the *synchronous* push, and it's already fixed.**
`channels-write-scaling.md` §2 showed the *blocking* push gated capture (a
subscriber halved it, 16k→7k). 7feffa2's async pusher removed that — the index
feed is now async + batched, off the capture loop. Contract #3's real teeth
("no index write **synchronous on the capture loop**") are satisfied.

**What's actually still wrong:** `stream` is a buffer that re-enters the full
`write_many` machinery every flush, so it ties `write_many` (440k vs 470k)
instead of beating it. The two gaps, both on the producer path, untouched by the
batching work:

1. **No held connection.** The sink should hold one open `do_put` and
   `write_batch` down it; instead each flush re-runs `write_many` (re-validates
   registration, rebuilds, re-pushes).
2. **Not columnar in the sink.** The sink builds a per-sample `ChannelSample`
   (Pydantic) + dict before handing to `write_many`; a real stream sink appends
   to **column buffers** and ships a `RecordBatch` directly.

Fix those two and `stream` becomes append + socket bound (the durable append
scales ~4× per worker) instead of carrying `write_many`'s per-call overhead.
**The index is not in this critical path and does not change.**

One thing to **verify in code, not assume:** whether a store opened
`index=True` **and** `serve=True` does a redundant *local* `_pending_extend`
(`store.py:546/681`) on top of the daemon's `ingest_batch`. If so, the fix is to
make that local feed async/batched like the push — **not** to remove indexing.

---

## Optional lever (measure first): index-on-close / pull-tail / #267

These are **not required** for the real stream and must not be done casually
right after optimizing the batched index. Reach for them only if a *measured*
rate ever outruns the columnar ingest (today it does not — it handles 470k+):

- **(A) Index-on-close** — index a stream's segment only at close (existing
  `_scan_disk`). Mirrors `files.stream`. Cost: not *at-rest queryable while open*
  (live subscribe still works). A throughput lever only if live indexing becomes
  the bottleneck.
- **(B) Pull-tail / #267 `pyarrow.dataset` vs Lance** — replace the DuckDB index
  *substrate* with a derived dataset over the segment files. This is the
  **index-substrate** exploration (a *separate axis* from this producer work),
  and is the cleaner **backend-swap** seam (dataset → Iceberg/Delta lakehouse).
  Cost: bigger; `channel_id` is in the filename, not a column, so a dataset can't
  prune until the layout is Hive-partitioned.

Hard constraint regardless (`channels-write-scaling.md` constraint #1): **live
subscribers stay instant; the index may lag.** Relay always stays on the
write-adjacent path; only at-rest *indexing* is what could ever move.

---

## The real-stream build (the actual work)

`_ChannelSink` becomes a connection, not a buffer:

- **Open once:** a held producer connection (long-lived Flight `do_put` /
  `DoExchange`) **plus** the durable segment writer (`_ChannelWriter` /
  `BufferedIPCWriter`). `ChannelStarted` emitted here = a live wire + open
  segment genuinely exist (the event becomes *truthful*).
- **`sink.write(v)`:** append to a **columnar buffer** — no per-sample
  `ChannelSample` (Pydantic) or per-row dict. (`channels-write-scaling.md` §1:
  Pydantic isn't the ceiling, but per-sample object build is pure tax on the
  fast path; build the Arrow batch from column buffers directly.)
- **Flush:** one `RecordBatch` `write_batch`'d straight down the held connection
  **and** the segment writer. Nothing else.
- **Daemon `do_put` for a stream = relay-only:** put the batch on the subscriber
  rings (`_notify_batch`/`_relay_batch` already do this, `store.py:1287`) — **no
  `_index_row`, no INSERT.** The consumer's `do_get` GeneratorStream is already
  waiting.
- **`close()`:** finalize the segment + index it (per the chosen strategy) +
  `ChannelEnded`.

Result: stream throughput stops being daemon-index-bound (~470k shared ceiling)
and becomes **append + socket bound — scales ~4× per worker** (the durable
append's own scaling), because the daemon does nothing but shuttle bytes and the
producer constructs no per-sample objects.

**Wire-trim (Phase 3, multiplicative):** rows on the wire carry
`[received_at, value, offset]` only; the descriptor is already sent once on open
(schema metadata), and the daemon re-attaches `channel_id`/`units`/`session_id`
from it. Measured **2.7–3.4×**, multiplicative with coalescing
(`channels-write-scaling.md` §1). Plus: stop re-exploding coalesced batches
per-row in the relay.

---

## Why this is the swap-clean shape (what we'd swap to)

Channels swap to a **time-series database** (`data-store-backends.md`): QuestDB,
TimescaleDB, InfluxDB, VictoriaMetrics, ClickHouse, AWS Timestream. Two swap
axes, don't conflate them:

1. **Serving-tier swap (req6, recipe recorded in `data-store-backends.md`):**
   same implementation, daemon goes from local subprocess to a remote host.
   `acquire(dir) -> opaque grpc:// location` already returns an opaque address;
   the hook is purely additive (one helper + four one-line hooks + a test).
2. **Backend swap:** replace the embedded stack (Flight + `.arrow` files +
   DuckDB + our pub/sub) with a TSDB, behind the *same* `stream`/`write`/`query`
   verbs:

| Embedded piece | Swaps to |
|---|---|
| producer held connection (the stream wire) | **QuestDB ILP** / **InfluxDB line protocol** — a long-lived TCP ingest socket; ClickHouse async-insert; Timescale `COPY` |
| `.arrow` segments (truth) | TSDB column store (ClickHouse/QuestDB), S3-backed (Timestream), or a lakehouse (Delta/Iceberg) at rest |
| DuckDB warm index (at-rest) | native TSDB time-range query (SQL: Timescale/QuestDB/ClickHouse; Flux/InfluxQL: Influx) |
| relay rings (live push) | TSDB live tailing where it exists; else broker-paired (NATS JetStream / Redis Streams) |

**The concrete reason the held-connection design matters:** QuestDB's and
InfluxDB's primary ingest *is* a held streaming socket (ILP). If `stream` is a
held connection, the backend swap is "open the ILP socket to QuestDB instead of
the `do_put` to our daemon" — same shape. If `stream` stays buffer→batch-RPC,
the producer doesn't match the ingest shape and the swap means restructuring it.
That is the actual content of "swap-clean."

**Swap discipline to maintain (`channels-write-scaling.md` req6 audit):** the
`offset`/cursor stays an **opaque internal** value — never a verb argument — so a
Kafka offset / Redis id / TSDB timestamp can substitute. It is already dropped
at both public read boundaries (`channels.query`, the HTTP/MCP `channels_query`).
Honest nuance: the at-rest/index half swaps to a TSDB cleanly; the live-tail half
swaps natively only on TSDBs that support tailing, otherwise it lands on a broker.

---

## Proposed phases

0. **Prior-art survey FIRST** (see the goal section) — how do InfluxDB / QuestDB /
   ClickHouse / Timescale / Kafka / kdb+ make streaming beat batch-insert, and
   what nuance do they trade? Land on a deliberate "speed-for-visibility,
   never-for-durability" nuance for Litmus.
1. **Real stream connection (the work).** `_ChannelSink` holds one open `do_put`
   wire + the segment writer; columnar buffer → `RecordBatch` straight down the
   wire **and** the durable segment; daemon does relay-only for the live path.
   Durable append on every flush — no data-loss trade. *This is "a real stream."*
2. **Wire-trim + batched relay** — `[received_at, value, offset]` on the wire;
   no per-row relay re-explosion. Multiplicative throughput (2.7–3.4× × coalescing).
3. **The behavior nuance that buys the last of the 2×** — chosen from the
   prior-art survey; the *only* legal trade is queryability/liveness/latency, not
   durability (e.g. index-on-close, eventual-consistent index, live-from-now).
4. **Swap-clean proof (req6)** — opaque resume cursor in app-metadata; map it
   onto one named backend per the recipe. Prove swap-ready; don't ship dead env
   vars.

The **index stays as built** (batched columnar `channel_index ∪ live overlay`).
No phase deletes it. Phase 3's nuance may change *when* a stream's data becomes
queryable — never whether it's durable.

---

## Current state (commit 7feffa2, branch `spike/batch-native-channels`)

Done & green (full suite 2044 passed): `sequence→offset` rename; `declare` +
within-session unit/type enforcement; value-only public verbs; `write_many`
(bare or paired); **async accumulating pusher** (push off the blocking path,
coalesced); **batching `_ChannelSink`** (flushes via `write_many`); benchmark
workers. **Not done:** the real held-connection stream, the columnar sink,
wire-trim, the behavior nuance, the swap proof — i.e. everything in this doc. The
batched columnar index is **done and stays** (it is not in the remaining work).

Measured today (`litmus benchmark --full`, throttled WSL2 box — trust ratios):
`write` 53k→143k, `write_many` 256k→470k, `stream` 181k→440k (1→4 workers).
`stream` ties `write_many` precisely because it *is* `write_many` underneath —
**the target is `stream` ≈ 2× `write_many`** via the held connection + columnar
sink + a durability-preserving behavior nuance.

---

## Glossary (what the moving parts actually are)

- **segment** — `channels/{date}/{channel_id}_{session_short}.arrow`. The durable
  Arrow-IPC append file. **The source of truth.** Written by `_ChannelWriter`
  (`BufferedIPCWriter`). Scales ~4× across workers (separate files).
- **`channel_index`** — an on-disk DuckDB **table** (`_index.duckdb`). The
  **at-rest query index** (time-range / last-N / decimate). **Derived** from
  closed segments by `_scan_disk` at startup (`store.py:1079/1113`). NOT truth.
- **`live.channel_live`** — an in-memory DuckDB **overlay** table, same schema
  (`store.py:1066`). Holds rows whose segment hasn't been scanned into
  `channel_index` yet. **Fed inline by the write/push path → this is the
  dual-write.** Ephemeral (lost on restart, re-derived by the next `_scan_disk`).
  A query unions `channel_index ∪ live.channel_live` (`store.py:1345`).
- **`offset`** — a per-`(channel, session)` monotonic **column/cursor**
  (`itertools.count`, the `event_offset` idiom). Lives in the segment, on the
  wire, and as a column in both index tables. Orders samples, dedups live↔history,
  and is the **opaque resume cursor** for the backend swap. Must never appear in a
  public verb signature (already dropped at the read boundaries).
- **relay / `_SubscriberRing` / `do_get`** — the **live** path: the daemon fans
  a batch onto each live subscriber's bounded ring; `do_get` yields them as a
  `GeneratorStream`. Cheap. Stays on the write-adjacent path. NOT the index.
- **the dual-write** — the producer writing **both** truth (segment) **and**
  index (`live.channel_live`) on the same call. Contract #3 forbids the
  *synchronous* form (it gated capture); 7feffa2's async pusher already fixed
  that. The remaining async + batched index feed is **fine and stays** — this
  effort does **not** remove indexing (see the corrected core-problem section).

## Track 2 knowledge — DO NOT LOSE (identity, discovery, ticket model)

This was designed in conversation; it lives in agent scratch and must survive
into the repo. It is **adjacent** to the streaming work, not a prerequisite —
but the streaming design must not foreclose it.

**Identity — uniqueness vs addressing are different jobs:**
- **Uniqueness = `(channel_id, session_id)`.** `session_id` is a durable uuid, so
  `(channel, session)` is a globally-unique channel-instance and
  `(channel, session, offset)` a globally-unique sample. Storage/refs key on it.
  **No separate channel GUID** — `(channel_id, session_id)` already *is* it.
- **Addressing/discovery = `hostname` + name + type/units.** `hostname` is
  always-present, **system-sourced** (station `station_hostname`, else
  `socket.gethostname()`), **never a `declare`/user arg**, unspoofable. It is the
  human namespace + default-filter-to-own-host — **not** the uniquifier (sessions
  already uniquify). **Server-era and dormant on a single machine.** Lives on the
  *session* (sessions carry env), not stamped per-channel; channels inherit host
  through their session. Likely a `StationInfo`/`SessionInfo` event after session
  creation (parallels instrument-in-use events for asset utilization).

**Metadata — immutable within a session, mutable across.** units/type/role are
captured at the establishing write; a conflict *within* a session is a
`ValueError` (OTLP "one writer per stream"); a *new* session may re-declare
different type/units (devs fix mistakes — OPC UA `SemanticsChanged`). The session
is the version boundary. (The within-session enforcement is the part already
built in 7feffa2.)

**Discovery = filter an indexed, versioned registry.** One row per
`(hostname, channel)` with `hostname`/`name`/`units`/`role`/`resource` as indexed
columns, `attributes` JSON, current definition + per-session version history, and
**liveness/last-seen**. The daemon is the registry + hub (OPC-UA address space /
MQTT broker): subscribe by `(hostname, channel)`, default-filter to own host, the
subscription survives producer restart. (Today the daemon registry is
last-write-wins, one descriptor per name — versioning + indexing + liveness is the
real Track 2 addition.)

**The ticket model (fixes vectored observations).** A channel reference is a
**ticket**: `(channel, session, [offset_start, offset_end])` — a *range*, not a
point. One shape covers everything: single `observe` → `[N, N]`; `stream` over a
window → `[start, end]`; whole series → `[0, end]`. `observe`/`write` returns the
offset-qualified ticket; it's stamped on the `Observation` event (live) and the
`out_*` parquet column (at-rest), both with `vector_index`. A consumer follows
the ticket to the exact sample(s). **Why it matters:** a 10-vector sweep that
observes `scope.trace` per vector currently stamps 10 *identical* `(channel,
session)` URIs (indistinguishable — a real bug); offset-qualified tickets give
each vector its own range. **Per-iteration tickets:** one frame per iteration →
point `[N,N]` (just the offset); a burst per iteration → range `[start_i,end_i]`.
**Today `make_channel_uri`/`_ChannelSink.uri` carry neither offset nor range**
(`ref.py:74`, two args) — the offset-in-ticket work is the bounded Track 2 code:
`make_channel_uri`/`parse_channel_uri` carry optional `[start,end]`; `write`
returns offset-qualified tickets; `_ChannelSink` records start at open / end at
close.

**Observation routing stays shape-based** (don't make everything a channel):
scalar → inline `out_*`; array/waveform → channel; blob → FileStore; URI/sink →
latched. A test author *may* opt a repeated scalar up to a channel (real
time-series), but it's never required — the floor stays low. Live observations
come from the **EventStore** (`Observation` events carry ticket + value-or-URI +
vector context); array data follows the ticket into the ChannelStore.

**The unified streaming model (channels mirror FileStore).** Streaming = durable
growing segment + live relay/range-read; **catalog/index lands only on close**
(forced for files by S3 multipart — you can't stream into an object store). Late
join = range-read the growing segment (history) + live relay (new), dedup on
`offset`. Discovery via the lifecycle event as the ticket. This is the same
shape Phase 1's "index off the write path, index-on-close" gives channels.

## Key files

- `src/litmus/channels.py` — public verbs, `_ChannelSink` (the buffer to replace
  with a connection).
- `src/litmus/data/channels/store.py` — `write`/`write_many` (`655` append,
  `546/681` index feed), `ingest_batch` (`1277`), `_pending_extend`/`_flush_pending`
  (`1267/1314`), `_scan_disk` (`1113`), `_query_index` (`1326`), index schema
  (`1079`).
- `src/litmus/data/channels/server.py` — `do_put`/`do_get`, `_live_stream`
  (`183`), `_relay_batch` + `_SubscriberRing` (`101/32`).
- `src/litmus/data/files/streaming.py` — the reference real-stream (`_BaseSink`,
  `publish_frame`, finalize-on-close).
- `src/litmus/data/channels/_ipc_writer.py` — `BufferedIPCWriter` (the durable
  streaming-append substrate).
- `src/litmus/benchmark/concurrency.py` — `run_concurrency` + channel workers.
- `litmus benchmark --full` — the user-facing measurement (writes
  `.benchmarks/<date>/report.md` with hardware + every row).

---

# Resolution (built) — 2026-06-13

## The reframe: align the verbs, don't race them

The "make `stream` 2× `write_many`" target was an artifact. `stream` was a buffer
wrapped around `write_many`, so it was *slower*, and "2×" chased a gap only the
wrapping created. Every producer-side speedup (columnar build, cheaper encode,
wire-trim) applies to `write_many` too, so none of them move the *ratio* — the only
thing `stream` could do that `write_many` can't is drop the live/index push under
overload, and that's a behavior trade, not a speed lever. The audit also showed
`write`/`write_many`/`stream` are **one operation** — "append a block of 1+ samples
to the durable log, then best-effort publish a frame" — differing only in batching
granularity (1 / N / streamed). So they were unified, and the 2× target dropped.

## What's universal here (don't reinvent — instantiate)

Streaming is the same shape at every layer: **durable ordered log + a position
cursor + live tail + late-join/replay + finalize.** RTP sequence number = Kafka
offset = our `offset`. The embedded tier (Flight relay + `.arrow` segments + DuckDB
index) is only Litmus's **no-infra stand-in**; its job is to hold that contract so
it swaps to whichever a deployment runs:

- **Server tier = 2 systems.** One primary DB (Postgres+TimescaleDB / DynamoDB /
  Cosmos) collapses events+runs+channels+file-catalog (durable-append + change-feed
  +range-query are all native); an **object store** holds file blobs (the only thing
  forced out of the DB). Could be 1 at toy scale (blobs as bytea).
- **`pyarrow.dataset` is OUT.** It needs a Hive file-layout change and is a local
  query-engine over our files with no server analog → it cuts *against* swap. The
  index stays behind the `query()` verb (DuckDB now → Timescale/Dynamo later);
  `offset` stays an **opaque internal cursor**, never a verb argument.
- The relay is a **kdb+ tickerplant**: dumb — append to the log, best-effort
  publish, drop if a subscriber is slow. Recovery/dedup/ordering live in the
  *consumer reading the log via `offset`* (the `window` verb), never in the relay.
- **Durable streaming = append immutable segments (durable per flush/PUT), never
  stream into one finalize-on-close object.** Local flushes per write; channels
  rotate `.arrow` segments. **S3 has no append** — `open_output_stream` does
  multipart-complete-on-close, a genuine data-loss-on-crash window — so the
  files-streaming phase must write segment-objects + a manifest (HLS/Iceberg shape),
  not one multipart blob. Channels is already on the right side of this.

## API altitude (high / low — one core)

- **High (intent, "what"):** `observe` / `verify` / `stream` — route by shape
  (scalar→inline `out_*`, array→channel, blob→FileStore, streamed→channel-segment).
  Under the hood is immaterial to the producer; the floor stays low.
- **Low (explicit, "where"):** `channels.write/write_many/stream`, `filestore.stream`
  — for drivers, power users, the UI's per-store views.
- Both are thin front-doors onto one mechanism. "Fast path" = the core is cheap,
  not a separate path.

## What was built

1. **`ChannelStore._append_and_publish`** (`store.py`) — the one body behind
   `write` (N=1), `write_many` (N), and the `stream` sink (which flushes via
   `write_many`). Scalar blocks take a columnar fast path (one `pa.array` per
   column, no per-sample `ChannelSample`/dict); array/struct take the per-row
   build; `ChannelSample` objects are materialized only when a per-sample
   subscriber or the local index consumes them. `_publish` is the best-effort tail
   (batch-subscriber fan-out → relay enqueue → index feed). Durable segment is
   written FIRST. The consumer wire (`sample_schema`) is identical to
   `samples_to_batch` — no consumer-visible shape change (a `ChannelSample` never
   crossed the wire; the pusher always serialized it to that batch).
2. **Batch-buffering `_ChannelWriter`** — accumulates whole `RecordBatch`es and
   flushes+rotates at the row threshold (or idle timer), so per-sample `write`s
   coalesce into segment-sized files instead of one file each, while staying
   queryable in memory (`pending_table()`) before the flush. Large blocks
   (≥ threshold) still flush immediately (durable-on-return); small/low-rate writes
   buffer up to `flush_interval` (≤1 s in-memory window — same as the old `write`).
   Live-subscriber latency is unchanged (the relay is independent of the writer).
3. **Dumb relay** — every verb now enqueues a wire `RecordBatch`; the pusher
   (`_push_loop`) only concatenates per channel and does one held `do_put`,
   drop-on-overflow. Deleted: `_flight_push` (the 1-row `ChannelSample` path) and
   the sample branch of the pusher.
4. **Runtime-readable log** — `_query_index` lazily folds newly-closed segments
   (`_maybe_scan_disk`, throttled `_RUNTIME_SCAN_INTERVAL`) and dedups overlay ∪
   index on `(session_id, offset)` (`_dedup_on_offset`, in pyarrow — DuckDB
   window-over-cross-DB-union hits an internal error). So a sample the push dropped
   under overflow is queryable from its durable segment **without a daemon
   restart** — no more restart-recovery. Fixed a latent bug: `_segment_rows_to_index`
   now carries `offset` (it was nulled on every disk scan; `offset` added to
   `_INDEX_ENVELOPE` so struct payloads don't fold it in).

## Measured (run_concurrency, throttled WSL2 — trust ratios)

`write_many` 489k→463k, `stream` 336k→468k (1→4 workers). `write_many` nearly
doubled vs the pre-alignment 250k (the columnar core helps it too); `stream ≈
write_many` at realistic concurrency (ratio ~1.0 at 2–4w). At 1w `stream` is ~0.7×
— the sink's buffer+timer+lock is ergonomic overhead over a raw `write_many` block,
which shrinks under contention. No 2× expectation; the verbs are aligned.

## Open / next

- **Tuning levers are scattered** (9 knobs across 4 files; `flush_interval` isn't
  even plumbed through `_ChannelWriter`). Task: collect into one `ChannelTuning`
  config object and plumb it; surface the durability pair (`flush_threshold`,
  `flush_interval`) toward `litmus.yaml`. Files-streaming reuses the same object.
- **Files-streaming is next** and is the same shape: segment-objects + manifest +
  finalize-on-close + a byte cursor; reuse the dumb-relay skeleton (built channels-
  clean for now, lift to shared when files needs it). Fix the synchronous
  `publish_frame` (the "same disease" channels had) and the non-atomic streaming
  sidecar (Rule F2).

---

# Next-session handoff (2026-06-13): queue + findings (context that lived only in chat)

**Branch:** `spike/batch-native-channels`. **Committed:** `e6b0fc5` (the alignment
above). Full suite 2048 passed. The items below were shaped in conversation and are
NOT yet built — written here so a fresh context can resume.

## Remaining queue (user's order)

1. **Track 2 — discovery / identity / liveness / ticket** (the big one; shaping below).
2. **Tuning levers** — collect the 9 scattered knobs into one `ChannelTuning` object
   and PLUMB `flush_interval`. Inventory: `_ChannelSink._FLUSH_ROWS`/`_FLUSH_INTERVAL`
   (`channels.py`), `ChannelStore(flush_threshold=)`, **`BufferedIPCWriter.flush_interval`
   (currently UNREACHABLE — `_ChannelWriter` never passes it; stuck at 1.0 s)**,
   `_pending_threshold`, push queue `maxsize=10_000`, `_PUSH_MAX_ROWS`/`_PUSH_MAX_WAIT`,
   `_SubscriberRing maxsize=1024`. Surface the durability pair (`flush_threshold`,
   `flush_interval`) toward `litmus.yaml`. Files reuses the object.
3. **PR to 0.2.0** — PR `spike/batch-native-channels` → the v0.2.0 integration branch
   (confirm exact branch name via `git branch -a | grep 0.2.0`). PRs are explicit-only;
   the user asked for this one. Stack if sequential.
4. **Files-streaming** — the next store, same shape (see the Resolution "Open/next").
5. **Stacked cross-session compare** on `/channels/{id}` — overlay one trace per
   `session_id`; `query()` already returns all sessions; align by `offset`/elapsed.
   Operator-facing identifiers (DUT/date), never raw session UUIDs.

## Track 2 shaping (from this session's conversation)

The crystallized idea: **a consumer DECLARES the channel/shape it expects, and that
declaration drives a standing watch over the EventStore (`ChannelStarted`) and/or the
ChannelStore registry that BINDS when a matching channel goes live — and re-binds
across producer restarts.** A passive `on_channel(name)` that just sits there is what
fails "page opened before the producer started" and "producer restarted."

Pieces (from the "Track 2 knowledge" section above + this conversation):
- **Liveness/versioned registry** — today `_registry` is last-write-wins, one
  descriptor per name, no versioning/liveness/hostname. Track 2 = one row per
  `(hostname, channel)` with indexed cols, `attributes` JSON, current def + per-session
  version history, and **liveness/last-seen**. Subscribe by `(hostname, channel)`,
  default-filter to own host, subscription survives producer restart.
- **Hostname** — system-sourced (`station_hostname` else `socket.gethostname()`),
  NEVER a `declare` arg, on the *session* (channels inherit it). Likely a
  `StationInfo`/`SessionInfo` event after session creation.
- **Ticket model** — `(channel, session, [offset_start, offset_end])` range refs;
  `make_channel_uri`/`parse_channel_uri` carry optional `[start,end]`; `write` returns
  offset-qualified tickets; `observe` stamps the ticket on the `Observation` event +
  `out_*` column with `vector_index`. Fixes the 10-vector-sweep-stamps-identical-URIs
  bug. Today `make_channel_uri` carries neither offset nor range (`ref.py`).
- **Active-match** — today `do_get`/`_live_stream` registers a ring for ANY name with
  no active check; subscribe to a dead channel → silent empty stream. Track 2 validates
  against the live registry.

NOTE: I was about to spawn an Explore agent over the Track 2 surfaces (registry,
`server.py` list_flights/do_get, `ref.py` make_channel_uri, `events.py`
ChannelStarted, `event_store.py` on_event cursor/replay) — do that first to ground a
Track 2 plan. Track 2 has real design forks (registry schema, URI ticket format,
version-history storage) → plan it, don't auto-pilot.

## The live-UI finding (IMPORTANT — not otherwise recorded)

Drove the operator UI live (example 09 producer + `litmus serve`, Playwright):
- **At-rest/history UI: works** — `/channels/dmm.voltage` rendered descriptor, chart,
  1000-sample table.
- **Live badge stayed "○ idle"** with a producer actively streaming at 50 Hz, in place,
  no reload — so live-append-to-an-open-page is UNCONFIRMED.
- **Critical nuance:** Phase C's runtime segment-scan means **"query works" no longer
  proves "live push works"** — the table can populate entirely from disk segments while
  the live relay does nothing. The badge is the only true live indicator, and it's not
  firing. Likely orthogonal/pre-existing (the daemon relay path was untouched; only
  producer-side enqueue changed) — user chose to fold the fix into Track 2
  (declare→standing-watch). To investigate properly: clean env (no mid-session daemon
  kills), trace producer push → daemon `ingest_batch`/relay → UI `on_channel`.
- The detail page drives "live" off **sample arrival** (`ui_channel_data` →
  `on_channel`), NOT `ChannelStarted` events — even though `event_binding.py` has the
  event plumbing. That's the wiring Track 2 should change.

## Gotchas / how-to

- **Stale pre-rename index** (column `sequence`, not `offset`) breaks queries with a
  Binder Error; sanctioned migration = clear `data/channels` (no backcompat). The
  example-09 channels dir was cleared this session (gitignored, regenerable).
- **UI demo recipe:** `uv run --directory examples/09-instrument-streaming litmus serve`
  + `uv run --directory examples/09-instrument-streaming python scripts/live_dmm_monitor.py`
  (set `LITMUS_STREAM_SECONDS`). Watch `/channels/dmm.voltage`.
- Daemon-test hygiene: no `tmp_path` daemons; `resolve_data_dir()`; kill stray
  `flight_daemon`/`litmus serve` before daemon-spawning runs.

---

# Built — 2026-06-14 (Half A + Half B Slice 1)

**Branch:** `spike/batch-native-channels`. Full suite green throughout.

## Half A — single-offset channel ticket (`7bd5e34`)
A channel reference is now a single-offset `ChannelTicket(channel_id, session_id,
offset)`. `make_channel_uri(offset=)` / `parse_channel_uri` carry it; `write`/
`observe` (n==1) return offset-qualified URIs; `write_many`/sink stay un-offset
(the deferred range case). `load_ref` slices by `ticket.offset`. Fixes the
10-vector-sweep stamps-identical-URIs bug. **Ranges (`[start,end]`) were dropped
as YAGNI** — observe is always one row (an array is one offset's *value*); a true
range only appears in streaming/batch, and adding `end_offset` later is provably
additive (proven surface-by-surface). Decide channel-vs-file range *encoding*
together at the streaming step.

## Half B Slice 1 — channel identity & liveness (4 commits)
- **P1 `b43ea50`** — hostname on the durable descriptor. `ChannelDescriptor`
  gains `hostname`/`session_id`; the store **self-resolves `socket.gethostname()`**
  (not a station config — not every producer has one; and it must be the raw
  producer host the pid-liveness keys on). Rides in segment metadata.
- **P2 `652dd1a`** — derived non-unique `channel_registry` table in the channel
  daemon's DuckDB: one version row per `(channel, session)`, hostname a grouping
  column. Sourced from the per-session descriptor (NOT the channel-keyed cache).
  `last_updated` advanced by the scan fold + live `ingest_batch`.
- **P3 `9fb7b02`** — `do_get __registry__` verb + `ChannelClient.channel_registry()`
  + `channels_liveness_query` (mcp): registry rows × event-store terminal lifecycle
  (`channel.ended`/`session.ended`), joined in-process → `{live, open_stale,
  closed, dead}`. Channel store = truth; event store = augmentation.
- **P4 `9e16d0d`** — sessions self-heal: the orphan sweep emits
  `SessionEnded(outcome="aborted")` on **same-host + pid-death** (never the
  no-events timeout). Pool tracks open *sessions* (runless/idle ones `open_runs`
  can't see).

**Key architecture call (settled this session):** for channels the **ChannelStore
is the source of truth**, NOT events — the store registers, then *emits*
`ChannelStarted` as a downstream notification (opposite of the runs flow). Liveness
= lifecycle (event-store augmentation) + `last_updated` recency staleness.

## Deferred queue (in order)
1. **Half B Slice 2** — `declare`→standing-watch (consumer binds when its expected
   `(hostname, channel)` goes live, re-binds across restarts) + active-match
   (subscribing to a dead channel errors, not silent-empty).
2. **Wire `channels_liveness_query`** as an MCP tool + HTTP endpoint + UI badge;
   add the full producer→daemon kill e2e (unit suite has the pool + predicate +
   verb round-trip; the 30s-sweep e2e is manual for now).
3. **Live-badge finding — INSTRUMENT, don't guess.** Producer *does* push live
   cross-process (`serve=True`→`do_put`, verified). The idle badge is likely
   daemon-identity (producer vs `litmus serve` resolving different ref-counted
   daemons / data dirs). Repro live with logging on both processes; don't bake an
   unverified cause into a fix.
4. **`StationInfo` event** — richer declared station metadata (config id/name/
   location/label) keyed to the session, SEPARATE from the runtime socket host.
   Justified by the host-source split in P1.
5. **`offset` → `sample_offset` rename** — channel column + index + wire schema +
   `ChannelSample.offset` + the Half A ticket field/URI param, swept together. No
   backcompat; needs a `data/channels` clear.
6. **Cross-host pid liveness** — P4 self-heal is same-host only (`os.kill` is
   host-local); a remote producer needs a different liveness probe.
7. **Tuning levers** + **files-streaming** + **stacked cross-session compare**
   (from the prior handoff queue).
