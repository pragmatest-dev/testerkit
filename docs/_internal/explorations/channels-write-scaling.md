# Channel write throughput — scaling problem (hand-off)

Status: problem statement, unsolved. Measured 2026-06-12.

## TL;DR

Scalar channel writes don't scale across concurrent producers. The producers
themselves scale almost perfectly (≈4× on 4 workers) — but every write is
pushed **synchronously** to a single shared Flight daemon, and that daemon
caps the aggregate at ~22k samples/s no matter how many producers feed it.
The daemon throws away ~94% of the producers' demonstrated capacity.

We need to keep the single shared index + instant cross-process subscribers,
**without** the daemon sitting synchronously on the write path.

## Measured evidence

Same machine, same run. `scale=20000`, best-of-3, parent wall =
`max(end) − min(start)` on `CLOCK_MONOTONIC` across worker processes.

```
serve=False  (files only, NO daemon):
  1w:  96,675 /s    96,675/writer   factor 1.00
  2w: 192,123 /s    96,061/writer   factor 0.99
  4w: 352,162 /s    88,040/writer   factor 0.91   ← scales ~4×

serve=True   (shared daemon, synchronous push):
  1w:  15,275 /s    15,275/writer   factor 1.00
  2w:  26,061 /s    13,030/writer   factor 0.85
  4w:  22,140 /s     5,535/writer   factor 0.36   ← flat, even regresses 2w→4w
```

"factor" = per-writer efficiency at N writers ÷ per-writer at 1. ~1.0 = perfect
linear scaling. The producer is 0.91; the daemon path is 0.36.

Component decomposition (single-thread, in isolation):

```
pure in-memory columnar INSERT (register batch + INSERT…SELECT):  2,873,835 /s
from_pylist(dicts) + INSERT:                                      1,295,829 /s
per-row build (batch_row_to_sample[Pydantic] + _index_row[dict]):    92,862 /s   ← per-sample Python tax
producer write() to IPC file, no daemon:                             96,675 /s   (scales to 352k @ 4w)
```

The DuckDB insert is **not** the bottleneck (2.9M/s). The per-sample Python
(rebuilding a `ChannelSample` + a dict per row) is the single-thread ceiling at
~93k — and even that isn't reached on the serve path, because the synchronous
1-row gRPC round-trip + contention caps it at ~22k first.

## Current architecture

**Producer** (`ChannelStore.write`, `store.py`): each scalar `write()` appends
a row to a per-channel Arrow IPC segment file (`.arrow`, durable, buffered/flushed
at a threshold) **and** does a synchronous Flight `do_put` push of a 1-row batch
to a shared daemon. The push blocks until the round-trip returns. `_flight_push`
is a no-op unless the store was opened `serve=True` (so `serve=False` is
files-only, the producer ceiling above).

**Daemon** (`ChannelFlightServer`, `server.py`): `do_put` → `store.ingest_batch`
→ per-row `batch_row_to_sample` (Pydantic) + `_index_row` (dict) → buffered →
`INSERT … SELECT` into an in-memory DuckDB overlay, **and** fans the sample out
to active `do_get` subscriber queues (`_flight_subscribers`, registered via
`store.on_channel(None, _on_sample)`).

**Index** (`store.py`): DuckDB.
- `channel_index` — on-disk (`_index.duckdb`), populated **once at startup** by
  `_scan_disk` (incremental, ledger-gated) reading closed segment files. There
  is **no** runtime/background re-scan.
- `live.channel_live` — an **in-memory** (`ATTACH ':memory:'`) overlay, populated
  by runtime `do_put`. Ephemeral; lost on restart, then re-derived from the
  now-closed segments by the next startup `_scan_disk`.
- A query unions the two. The **source of truth is neither DuckDB table** — it's
  the producer `.arrow` segment files. Both tables are derived caches.

**Subscribers**: cross-process, connect via Flight `do_get`; the daemon relays
batches to their queues as samples arrive (this is the "live" path operators
watch).

## Why it's slow

The daemon is doing two jobs **synchronously on the write path**:
1. **Index ingest** — the expensive per-row Python + insert.
2. **Live fan-out** — cheap (put a batch on each subscriber queue).

Because the push is synchronous and 1-row, every producer's `write()` blocks on
a gRPC round-trip, and all producers serialize through one daemon process (one
GIL, one write lock). Job (1) drags down job (2), and the round-trip overhead
caps the whole thing below even the daemon's own 93k ingest ceiling.

## Constraints (hard — these shaped prior rejected attempts)

1. **Live subscribers must be INSTANT** — per-sample, not file-flush cadence.
   The **index may lag** (eventually consistent) — that's explicitly fine — but
   subscribers may not. So "just let the daemon read files in the background and
   drop the push" is NOT acceptable on its own: file-tailing makes subscribers
   slow.
2. **No custom concurrency-blocking layer.** Let Arrow/the transport handle
   concurrency; don't add bespoke locks/queues that re-serialize what was
   parallel. Fan-out writes should not hurt each other.
3. **Stay on-path to a future remote/object-store server.** The producer→daemon
   hop must be able to become producer→remote-server without a rewrite (the
   "serving-tier location swap", a.k.a. req6: `acquire(dir) → opaque location`).
   The index substrate is an open spike (#267: `pyarrow.dataset` vs Lance over
   the segment files). Don't bake single-machine assumptions (e.g. "the indexer
   reads the producers' local files") into the design — a remote server can't
   read a station's local disk; data must be streamed to it.
4. **Arrow-native carriers throughout** — segments are Arrow IPC, the push is
   Flight; descriptors ride as schema metadata. Keep it Arrow end-to-end.
5. **No backcompat shims** — pre-release, no users. Rename/replace cleanly.
6. **The bulk path already scales.** High-rate channels can write arrays via
   `channels.block` (one RPC carries N points instead of one RPC per scalar) and
   it's orders of magnitude faster. **This problem is specifically the
   per-sample scalar `write()` path** — the convenient API that funnels 1-row
   pushes.

## Direction (a hypothesis, not a prescription — solver should validate)

Split the daemon's two jobs and take the **expensive** one off the write path:

- **Live fan-out** stays near the write path but does **only** the cheap relay
  (deliver the batch to subscriber queues — no per-row ingest, no DuckDB). The
  producer push becomes **fire-and-forget / async** so it never blocks `write()`.
  Drop-on-overflow is acceptable for live (the files are durable; "live" means
  from-now). This is what keeps subscribers instant.
- **Index ingest** moves **off** the write path entirely: a background reader
  builds the index from the segment files, eventually consistent, allowed to lag.
  This is where `pyarrow.dataset` / Lance comes in — query the files as a dataset
  with no per-row ingest at all (the on-disk `channel_index` cache could go away).

Net: the write path becomes "append to your own file (scales to 352k) + a cheap
async relay for whoever's subscribed." The shared daemon stops being a
synchronous chokepoint.

## Open questions for the solver

- **Does the live relay stay instant once decoupled from ingest?** The relay is
  cheap in principle (queue puts); needs measuring under concurrent producers.
- **Can a background file-reader index keep up with ~352k/s aggregate?** The
  columnar insert is 2.9M/s in isolation, but a prior async end-to-end attempt
  only reached ~89k and was **never honestly diagnosed** before it was reverted.
  Treat 89k as an unexplained data point, not a ceiling.
- **`pyarrow` async Flight is a dead end for the push:** `AsyncioFlightClient`
  exists but implements only `get_flight_info` — there is **no async `do_put`**
  (apache/arrow#34607, open since 2023). So "async push" = offload the push to a
  background thread on the producer, not a native async RPC.
- **Segment layout vs dataset filtering:** `channel_id` is encoded in the
  **filename** (`{date}/{channel_id}_{session}.arrow`), not as a column. A
  `pyarrow.dataset` over the files needs `channel_id` as a partition key or
  column to filter/prune — the current layout is not Hive-partitioned.
- **Array vs scalar envelope:** array-valued channels store a
  `{value, sample_interval}` envelope split differently from scalars in the
  index encoding — any columnar fast-path must gate on scalar vs array, not blindly
  `SELECT value`.

## Relevant files

- `src/litmus/data/channels/store.py` — `write()`, `_flight_push`,
  `ingest_batch`, `_scan_disk`, `_insert_index_rows`, `_index_open`,
  `_connect_or_serve`
- `src/litmus/data/channels/server.py` — `ChannelFlightServer`: `do_put`,
  `do_get`, `_on_sample` fan-out, `_flight_subscribers`
- `src/litmus/data/channels/models.py` — `ChannelSample`, `sample_to_batch`,
  `sample_schema`, `batch_row_to_sample`
- `src/litmus/data/channels/_flight_daemon.py` — daemon entrypoint
- `src/litmus/benchmark/concurrency.py` — `run_concurrency` measurement harness
  (`_channel_worker` is `serve=True`)

## Reproduce the measurement

```python
import time, tempfile, shutil
from multiprocessing import get_context
from pathlib import Path
from uuid import uuid4

_c = time.CLOCK_MONOTONIC
def _now(): return time.clock_gettime(_c)

def sf_worker(args):
    data_dir, scale, seed = args
    from litmus.data.channels.store import ChannelStore
    store = ChannelStore(Path(data_dir), uuid4(), flush_threshold=50, serve=False)
    store.open()
    t0 = _now()
    try:
        for i in range(scale):
            store.write(f"dmm.voltage_w{seed}", 3.3 + (i % 100) * 0.01)
    finally:
        store.close()
    return (t0, _now())

SCALE = 20000
fork = get_context("fork")
dd = Path(tempfile.mkdtemp())
for W in (1, 2, 4):
    walls = []
    for _ in range(3):
        with fork.Pool(W) as pool:
            spans = pool.map(sf_worker, [(str(dd), SCALE, w) for w in range(W)])
        walls.append(max(e for _, e in spans) - min(s for s, _ in spans))
    print(f"serve=False {W}w: {SCALE*W/min(walls):,.0f}/s")
shutil.rmtree(dd, ignore_errors=True)

# serve=True via the real harness (spawn, hits the daemon RPC path):
from litmus.benchmark.concurrency import run_concurrency
dd2 = Path(tempfile.mkdtemp())
for W in (1, 2, 4):
    walls = run_concurrency(dd2, "channels.write", SCALE, W, rounds=3)
    print(f"serve=True  {W}w: {SCALE*W/min(walls):,.0f}/s")
shutil.rmtree(dd2, ignore_errors=True)
```

---

# Findings & validated design (2026-06-13)

Status: the hypothesis above was tested. The bottleneck was **mis-diagnosed**
as per-row Python; it is **per-message** plus a **dual-write that gates capture**.
A scored mechanism matrix and an env-gated experimental implementation follow.
Benchmarks under `.tmp/bench_*.py` (scratch, not committed).

## 1. The bottleneck is per-MESSAGE, not per-row Python

Removing Pydantic does **not** move the ceiling. Isolated stage measurements
(single thread, in-process server, `bench_bottleneck.py`):

```
build ChannelSample per row (pydantic)        755,230 /s   ← NOT the bottleneck
sample_to_batch per row (1-row Arrow, K=1)     33,259 /s   ← 1-row Arrow build is a tax
samples_to_batch coalesced K=100              877,990 /s   ← 26× from batching
null-ingest server  K=1                         18,922 /s   ← transport, 1 msg/row
null-ingest server  K=100                      685,424 /s   ← 36× from coalescing alone
null-ingest server  K=1000                   1,024,706 /s
real-ingest server  K=1                          9,276 /s
real-ingest server  K=1000                      77,282 /s   ← per-row server CPU is the SECOND ceiling
```

The wall is the **count of 1-row messages**, not the per-row CPU. Two stacked
ceilings: (1) ~19k/s — one 1-row Arrow message per sample (build + framing);
(2) ~77k/s — per-row `batch_row_to_sample` + `_index_row` on the daemon, only
visible after batching. Coalescing rows-per-message is the only lever that
moves (1); columnar insert moves (2).

**Redundant metadata compounds it.** Every sample re-ships `channel_id` /
`units` / `source_method` / `session_id` as columns, though the descriptor is
sent once on stream-open and the server reads `channel_id` from the *descriptor*
(`server.py:76`), never the row. Trimming to `[received_at, value]`
(`bench_trim.py`): K=1 19.7k→52.8k (2.7×), K=100 699k→2.4M (3.4×) — multiplicative
with coalescing.

## 2. The dual-write GATES CAPTURE (the real defect)

`write()` (`store.py`) appends to the durable `.arrow` file (fast) and then,
**synchronously on the same call**, does the Flight `do_put` to the daemon
(`_flight_push`, ~line 405). The push blocks under gRPC flow-control, so the
capture loop runs at the **daemon's drain rate, not the disk's** — and the
daemon's drain rate drops when a subscriber attaches (per-sample re-explosion
fan-out). This violates the data-store contract #3 ("the index is a derived
view with a single writer — never a dual-write", `data-store-backends.md`).

Measured on the **real** `ChannelStore(serve=True).write()` (`bench_real_capture.py`):

```
                 capture/s     note
sync  +sub          7,145      a subscriber HALVES capture (16.4k→7.1k)
sync  nosub        16,379      already ~6× below the ~96k durable-append ceiling
async +sub         84,921      decoupled: ~12× faster, subscriber-independent
async nosub        88,943
```

Durable append alone scales to ~96k/s single-thread, ~352k/s @ 4 writers
(`serve=False`). The synchronous push throws most of that away, and *watching
the test slows the test* by ~2.4×.

## 3. Live-subscribe latency is already excellent (current path)

Rigorous tail, paced (no backlog), real cross-process daemon, 18k–36k samples
(`bench_tail.py`); scheduler-jitter probe attributes the floor:

```
sched floor (no Flight)   p99=0.32  p99.9=0.43  max=0.43 ms   ← OS/WSL jitter is tiny
30Hz  steady              p50=0.60  p99=1.15   p99.9=1.72  max=2.08 ms
200Hz steady              p50=0.49  p99=1.26   p99.9=1.63  max=2.56 ms
```

Write→subscriber is ~1ms p99, ~1.7ms p99.9 at any realistic live rate — instant,
6× inside a 10ms budget. The earlier 33ms "outlier" was a single cold-start
sample on a 600-sample run, not a recurring tail. (Position 2 keeps the
firehose out of the live path; the live feed is ~30Hz × tens of channels.)

## 4. Scored mechanism matrix (live pub/sub + derived index)

Requirements (from `data-store-backends.md`): **R1** single-writer derived view
(no dual-write) · **R2** in-order + resume-cursor · **R4** config-swap to a named
backend · **R5** local zero-copy · **R6** Arrow-native · **R7** no bespoke
re-serializing concurrency layer · **Py** Python maturity · **L↔R** one API
local+remote. (✓/◐/✗)

| Candidate | R1 | R2 | R4 | R5 | R6 | R7 | Py | L↔R |
|---|----|----|----|----|----|----|----|-----|
| Current: sync 1-row `do_put` (dual-write) | ✗ | ✗ | ◐ | ✗ | ✓ | ✗ | ✓ | ✓ |
| Tickerplant log-broadcast (PUSH off append) | ✓ | ✓ | ✓ | ◐ | ✓ | ✓ | ◐ | ◐ |
| Tail-the-files (PULL by offset) — *index side* | ✓ | ✓ | ✓ | ◐ | ✓ | ✓ | ◐ | ◐ |
| Shared-memory ring (Disruptor/Aeron) | ✓ | ✓ | ◐ | ✓ | ◐ | ✓ | ✗ | ✗ |
| Flight DoExchange / long-lived DoGet (batched) | ◐ | ◐ | ◐ | ✗ | ✓ | ◐ | ✓ | ✓ |
| Redis Streams | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | ✓ |
| ZeroMQ PUB/SUB | ◐ | ✗ | ◐ | ◐ | ◐ | ✓ | ✓ | ✓ |
| NATS JetStream | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ◐ | ◐ |

Precedents: kdb+ tickerplant (single append → index + live as independent
cursor-based subscribers; `-11!` bounded replay = resume cursor); Kleppmann
"database inside-out" (materialized views tail the log); Arrow Plasma deprecated
since Arrow 10 (shm in Python is immature).

## 5. Recommended design (two layers)

- **Structure — delete the dual-write.** `write()` does one durable append and
  returns. Index and live fan-out are two independent consumers of that
  sequence. Satisfies R1; makes R4 a real config swap (producer→log→consumers
  *is* Kafka/Redis-Streams for events, S3-notify for files).
- **Live (push) — long-lived Flight DoGet/DoExchange, multi-row batches, no
  re-explosion.** Lowest risk, already in stack, kills both measured caps. Build
  the resume cursor (offset in app-metadata). Good enough for ~30Hz × tens of
  channels.
- **Index (pull) — tail the segment files by offset.** *This is where #267
  (`pyarrow.dataset` vs Lance) lives* — the derived index, allowed to lag. Caveat:
  `channel_id` is in the filename, not a partition key, so a dataset can't prune
  until the layout is Hive-partitioned.
- **Shared-memory ring — future seam, not now.** Only true zero-copy local
  multicast, but Python-immature (Plasma dead; `multiprocessing.shared_memory`
  is raw bytes). Keep on-path by making the live verb API `opaque-cursor +
  RecordBatch` so a shm ring slots in later (build item 22).

## 6. Experimental implementation — VALIDATED, env-gated, NOT committed

`store.py`, gated on `LITMUS_CHANNELS_ASYNC_PUSH=1` (default off = current
behavior): `__init__` adds `_push_queue/_push_thread/_push_stop/_push_drops`;
`open()` starts a `_push_loop` background thread when serving; `write()`
enqueues instead of inline `_flight_push` (drop-on-overflow = live from-now);
`close()` drains+joins the pusher. This moves the daemon push off the write path
*without* touching the durable append.

Validated capture win: §2 table (5–12×, subscriber-independent). Live behaviour
at realistic rates (`bench_async_latency.py`, single channel, under relay ceiling):

```
async  200Hz  recv=2000/2000  drops=0  p50=0.61  p99=1.68  p99.9=18.47  max=23.39 ms
async 1000Hz  recv=5000/5000  drops=0  p50=0.50  p99=2.90  p99.9=23.00  max=26.34 ms
```

Zero drops at realistic rates; median/p99 preserved. **Deep-tail note (corrected):**
p99.9 is ~18–23ms, but apples-to-apples isolation (`bench_tail_compare.py`) shows
**sync `ChannelStore.write` has the same ~17–25ms tail** — it is NOT the async
handoff, and NOT segment rotation (decoupling rotation left it unchanged). The
tail lives in the real write path itself (flush-timer thread / GC / scheduler);
p99.9 ~20ms is imperceptible for a live view, so it's deferred, not a blocker.

## 7. Open items

- **Deep-tail (p99.9 ~20ms)** — present in sync and async `ChannelStore.write`
  alike (not the handoff, not rotation). Needs per-stage timestamps inside
  `write()` to locate (flush-timer / GC / scheduler), then tighten. Low priority.
- **Batched-relay fan-out** — stop re-exploding coalesced batches per-row in
  `ingest_batch`/`_on_sample`; raises the ~4k/s live-relay ceiling.
- **Index as a genuine pull consumer** off the segment files — must not share the
  writer's critical path (else the dual-write returns under a new name).
- **EventStore** dual-write (`emit()` → IPC + batched `do_put`) is **unmeasured** —
  the one store not decomposed.
- **FileStore streaming** (`files.stream_raw`, 49 MB/s) is the *same disease*:
  a synchronous per-64KB-chunk daemon frame-push (`streaming.py` `_track_bytes`).
  The blob write itself fills the pipe (1.33 GB/s); the sidecar is a fixed
  per-artifact tax (`bench_filestore.py`).
- **Branch rename** — `spike/267-pyarrow-dataset` is too narrow; #267 survived as
  the index-substrate sub-task. Not pushed, so rename is free.
- **R4 proof** — map the opaque cursor onto two named backends (Redis
  last-delivered-id, S3 event) before claiming "config swap."

# req6 backend-swap audit (Phases 0/2/3, 2026-06-13)

Verdict: **everything added is swap-clean.** The public contract is verb + name
+ (query) time/count filters + `max_hz`; everything backend-specific is
implementation. Per surface:

- **Public verbs** (`channels.py`): `latest(name, cb)`, `live(name, cb, max_hz=)`,
  `query(name, start, end, last_n, max_points)`. No `received_at`/cursor/offset/seq
  in any signature; `policy` is set *internally* (`SubscribePolicy.LATEST`/`ALL`,
  channels.py:202/229), never a verb arg. `query`'s filters (time range, last_n,
  max_points) are backend-universal (TSDB range, Kafka timestamp, S3 list). **Backend-neutral.**
- **Policy `all`/`latest`** maps to broker semantics (Kafka all-offsets vs compacted;
  Redis `XREAD` vs last-id; TSDB tail vs last). The `?policy=latest` ticket
  (`server.py` do_get) is **transport**, not API — a Kafka adapter translates the
  verb to a compacted-topic consumer. **Swap-clean.**
- **`_SubscriberRing` + gap-count + drop-oldest** is purely server-side; the verb
  contract is callback + policy. The gap is a server counter, not on the wire,
  not in the API. A broker would retain rather than drop; the API never promises
  drop. **Embedded-only, doesn't leak.**
- **`max_hz`** is a client-side throttle (`_throttle_batches`); backend-agnostic.
  It doesn't *block* a swap. (Prior art: a server-side `samplingInterval` could
  supplement it — additive, not required.) **Swap-clean.**
- **Durable-append target** — `write()` still appends to the local `.arrow`
  `_ChannelWriter` (store.py:425). Phase 0's async push (store.py:440) is the
  *daemon push*, orthogonal to the append — it did **not** deepen the local-file
  coupling. (Swapping the durable substrate to a remote log *being* the truth is
  pre-existing req6 work, unchanged here.) **Not deepened.**

**One discipline to maintain:** Phase 4's stitch/catch-up cursor (the planned
monotonic per-channel sequence) must stay an **opaque internal** value — never a
verb arg — so a Kafka offset / Redis id / TSDB timestamp can substitute. The
plan (Decision 5, Phase 4 risk) already says this; the audit confirms it's the
single thing that could turn "swap-clean" into "blocker" if violated.
