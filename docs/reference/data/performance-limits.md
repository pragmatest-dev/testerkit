# Performance limits

Measured headline numbers for the v0.2.0 data layer. Use them to size
projects, to spot regressions, and to know when you're approaching a
cliff.

All numbers below were measured on a developer-class WSL2 host in
2026‑06 using `pytest tests/test_data/test_perf.py -m benchmark`
(`pytest-benchmark`, warmup on, `min_rounds=30` for query suites,
GC disabled for query suites). Mean reported unless noted. Reproduce
locally with:

```bash
pytest tests/test_data/test_perf.py -m benchmark --benchmark-only \
    --benchmark-columns=min,mean,stddev,median,iqr,ops
```

The 11-sample minimum-over-rounds methodology used by the daemon
regression gates (`tests/test_data/test_perf_daemon.py`) gives more
stable numbers than the means below — if you want a *gate*, copy that
pattern, not the means here.

To measure these numbers on **your own** machine — and get a result
file you can send in when you hit a performance problem — run
[`litmus benchmark`](../../how-to/data/benchmarking.md). It runs the
same store workloads against a throwaway directory and reports durable
throughput per store, parallel-writer scaling, and the run's RAM/CPU
footprint.

## EventStore

| What | Scale | Median | Notes |
|---|---|---|---|
| Emit 1k events (bulk) | 1k | ~3.4 s mean, 35 ms min | Bursty contention. The mean is unstable — single bursts under 50 ms when the daemon is warm; multi‑second outliers under load. **The emit benchmark itself is noisy** and should not be used as a gate as written. |
| Query by `event_type` | 10k events | 1.9 ms | Pure DuckDB filter on the typed column. |
| Query scale 100 | 100 | 2.3 ms | Constant-factor floor. |
| Query scale 1k | 1k | 13.6 ms | Linear. |
| Query scale 10k | 10k | 107 ms | Linear; the parse cost stops being negligible here. |
| Query by multi-session | mixed | 5.7 ms | |
| Pushdown `outcome=failed` | 10k | 38 ms | Typed column. |
| Pushdown `instrument_role=dmm` | 10k | 26 ms | Typed column. |
| Query by JSON payload field | 10k | 108 ms | 2.8–4× slower than typed-column pushdown — the headline win from PR #39 (typed payload columns). |
| Parse-only cost | 10k | 32 ms | Lower bound — what you pay even when the daemon hands you a perfect result set. |

**Cliff:** linear scan past ~10k matched events trends into 100 ms+
territory. Above that, the UI's 5 s `READY` budget gets nervous if a
query has to re-scan multiple times in a render. Use the typed-column
filters when you can — they're the difference between "instant" and
"perceptible lag."

## ChannelStore

| What | Scale | Median | Throughput | Notes |
|---|---|---|---|---|
| Write scalar `(name, value)` | 100 samples | 716 µs | ~140k samples/s | Per-batch call. |
| Write scalar | 1k samples | 7.4 ms | ~135k samples/s | Linear. |
| Write scalar | 10k samples | 76 ms | ~131k samples/s | Linear. |
| Write array-channel (1k waveforms × 1k samples each) | 1 M samples | 24.3 ms / 1k-call | n/a per-call | Array shape; per-write call writes one 1k-sample waveform. |
| Query scalars 1k window | 1k | 186 µs | 5.4k queries/s | |
| Query scalars 10k window | 10k | 1.9 ms | 510 queries/s | |
| Query with LTTB decimation | n/a | 4.8 ms | 210 queries/s | Add-cost of the decimation step on top of the raw query. |
| `channels.stream(...)` context-managed | 100 samples | 1.05 ms | ~92k samples/s | Same payload as the direct `write` row above. Context-manager overhead costs roughly **30 %** of raw `store.write` throughput. |
| `channels.stream(...)` context-managed | 1k samples | 10.7 ms | ~93k samples/s | |
| `channels.stream(...)` context-managed | 10k samples | 104 ms | ~96k samples/s | |

**Cliff:** ChannelStore writes scale linearly through 10k samples. At
~130k samples/s the bottleneck is the per-write append + flush
batching. If you need higher sustained rates — > 50 kHz acquisition,
many concurrent channels — the data path holds up but the Flight
subscribe-side starts to lag the producer. The shm-transport PoC
(item #22, deferred) was an attempt to relieve that path; the v0.2.0
recommendation is to stay under 50 kHz per channel until that lands.

## FileStore

### One-shot writes (`files.write`)

| Payload | Size | Median | Throughput | Notes |
|---|---|---|---|---|
| Bytes blob | 1 KB | 94 µs | 8.9k ops/s, ~9 MB/s | The lower bound. Most of this is the sidecar atomic-rename pair, not the I/O. |
| Bytes blob | 100 KB | 172 µs | 4.7k ops/s, ~470 MB/s | |
| Bytes blob | 1 MB | 808 µs | 1.1k ops/s, ~1.1 GB/s | Disk cache; the OS hasn't fsynced yet. |
| Bytes blob | 10 MB | 11 ms median, **23 ms mean** | ~430 MB/s amortized | Stddev climbs sharply — disk pressure dominates above ~5 MB per artifact. |
| ndarray | 1 KB | 119 µs | 6.9k ops/s | `.npy` serializer + sidecar. |
| ndarray | 100 KB | 210 µs | 4.1k ops/s | |
| ndarray | 1 MB | 793 µs | 1.1k ops/s | |
| Waveform (10k samples, .npz) | ~80 KB | 432 µs | 2.1k ops/s | The shape used by `examples/08-waveform-evidence`. |

### Reads

| Payload | Median | Throughput | Notes |
|---|---|---|---|
| `resolve_uri(...)` only (warm, same-day partition) | 9 µs | 104k ops/s | Pure dir-walk lookup. |
| Read 1 KB bytes | 28 µs | 33k ops/s | Resolve + open + read. |
| Read 100 KB bytes | 31 µs | 29k ops/s | OS page cache. |
| Read 1 MB bytes | 75 µs | 12k ops/s | OS page cache. |

**Cliff:** the cold-disk case is not measured (every benchmark hits
page cache). Production retention design should assume reads from
cold storage are 100×–1000× slower for large artifacts. The
`resolve_uri` walk is fast today because every test session lands
in the current date partition; the worst case (cross-month historical
reads) scales O(days). That's the long-term motivator for L1
(per-store attribute indexes — see [`data-stores.md` §12](../../_internal/explorations/data-stores.md)).

## Streaming sinks (`files.stream(format=...)`)

The "mean per call" column below is per **burst** — each benchmark
invocation opens a sink, writes `n` chunks, then closes. Divide bytes
by the mean to get sustained throughput.

| Format | Burst shape | Median per burst | Implied sustained |
|---|---|---|---|
| `raw` | 64 × 1 KB | 149 µs | ~430 MB/s |
| `raw` | 64 × 64 KB | 1.8 ms median, **10 ms mean** | ~420 MB/s median, dominated by outliers |
| `raw` | 64 × 1 MB | 163 ms median, **166 ms mean** | ~395 MB/s |
| `jsonl` | 32 × 10 rows | 121 µs | 7.6k bursts/s |
| `jsonl` | 32 × 100 rows | 437 µs | 2.2k bursts/s |
| `jsonl` | 32 × 1000 rows | 3.5 ms | 280 bursts/s |
| `tdms` | 16 × 10k float64 | 1.0 ms median, **3.9 ms mean** | ~325 MB/s with high variance |
| `h5` | 16 × 10k float64 | 3.3 ms | ~350 MB/s |

**Cliff:** raw streaming hits a sustained ~400 MB/s on this hardware,
limited by the per-chunk fsync-rename atomic. The cliff is *latency
variance*, not bandwidth — single chunks routinely take 10× the
median under contention. If you need predictable per-chunk latency,
keep chunks ≤ 64 KB; if you need throughput, batch up to 1 MB per
chunk and accept the variance.

`jsonl` is the cheapest format for event-style accumulation but
scales linearly with row count per chunk because every row gets
its own `json.dumps`. For dense numeric data, use `tdms` or `h5`.

## When to worry about a regression

Roughly, a 1.5× slowdown on any of the above numbers is regression-
worth-investigating territory. The release-prep gates in
`tests/test_data/test_perf_daemon.py` are the formal contract; they
use min-over-rounds rather than means so transient spikes don't fail
the build. **The means in this doc are not gates** — they're the
"what users should expect" baseline.

Three benchmarks are *known noisy*:

| Benchmark | Why it's noisy |
|---|---|
| `test_emit_1k` | Background daemon contention; mean varies 50× between runs. Use the median, ignore the mean. |
| `test_write_bytes[10240]` (10 MB) | Disk pressure. The stddev is bigger than the mean. |
| `test_stream_raw[64]` and `[1024]` | Per-chunk variance dominates. Median is meaningful, mean is not. |

When the v0.3.0 retention work lands, expect to add cold-storage read
benchmarks plus a "many concurrent producers" sustained-rate test for
ChannelStore. Neither is in v0.2.0 scope; the producer side is the
scoped surface for this release.

## Concurrency — multi-process scaling

All four stores hold up under N concurrent writers. Numbers below
measured at N = 1, 2, 4 spawned subprocesses (the `multiprocessing`
`spawn` start method, see the **fork-deadlock pitfall** below). Each
worker writes its own session / channel / artifact bucket so the
benchmarks model the multi-UUT pytest case where N workers run
concurrently against the canonical data dir.

| Store | N=1 wall | N=2 wall | N=4 wall | N=2 efficiency | N=4 efficiency |
|---|---|---|---|---|---|
| EventStore (500 events / worker) | 2.89 s | 5.21 s | 10.30 s | 89 % | 89 % |
| ChannelStore (500 samples / worker, distinct channels) | 296 ms | 304 ms | 370 ms | 97 % | 80 % |
| FileStore (100 × 10 KB / worker) | 328 ms | 333 ms | 426 ms | 98 % | 77 % |

(Efficiency = ideal-parallel-wall / observed-wall × 100. 100 % = perfect
linear scaling; 50 % at N=4 = pretending 4 workers run as 2.)

**Reading these:**

* No singleton catastrophe. The EventStore daemon serves N writers
  without going serial; total throughput climbs ~3.9× from N=1 to N=4.
* Constant per-process cost on EventStore (~89 % efficiency at all N
  ≥ 2) is the Flight RPC handshake — not the daemon's DuckDB write
  path. Adding more workers gives you more total work done; it doesn't
  speed up any one worker.
* ChannelStore + FileStore lose ~20 % efficiency at N=4 — ext4 dirent
  contention on the atomic rename pair dominates. Stddev climbs
  sharply at N=4 on both. Below N=4 it's free.
* RunStore barely parallelizes (~1.3× from N=1 to N=4): each
  `save_test_run` writes a parquet file and materializes the run, which
  is heavy per-op and serializes more than the log/segment/blob writers.
  Concurrent run materialization is the weakest scaling of the four
  stores — size accordingly if many stations finalize runs at once.

To reproduce all of this on your own machine, run
[`litmus benchmark --full`](../../how-to/data/benchmarking.md): it runs
the same 1/2/4 writer sweep per store and reports the speedup, plus the
single-writer throughput and the run's RAM/CPU footprint.

### ⚠ Fork-deadlock pitfall — must use `spawn`, not `fork`

The EventStore daemon is multi-threaded (Flight RPC threads + ingest
thread). Python's default `multiprocessing.Pool()` uses `fork()` on
Linux, which copies the parent's mutex state but **not** the threads
holding those mutexes. Child processes deadlock on the first
`event_store.emit(...)` call.

**Symptom:** workers spawn, then hang indefinitely. The parent never
sees output from the children. `pstack` on a child shows it waiting
on a mutex no other thread in the child holds.

**Fix:** use `multiprocessing.get_context("spawn").Pool(...)` instead
of `Pool(...)`. Slower spawn (each child re-imports Python + libs)
but no shared mutex state. The concurrency benchmarks above use
`spawn` for this reason.

**Production implication:** any code that calls `os.fork()` or
`multiprocessing.Process()` after the EventStore singleton is alive
in the parent will deadlock the children. The slot-runner mode is
safe because each pytest worker is spawned by pytest itself before
the parent imports Litmus heavily; ad-hoc helpers in test code that
fork their own subprocesses **after** emitting any events must
opt in to `spawn` explicitly.

This is the single most important concurrency caveat in v0.2.0. It
is not a Litmus bug — it is the standard Python `fork` + threads
hazard — but the v0.2.0 docs surface it here because Litmus's
singleton stores make it easy to hit.

## Hardware envelope used to produce these numbers

WSL2 on Windows, ext4 on the WSL VHDX. Single producer per benchmark.
No real network — Flight runs on loopback. Numbers will vary by ±2×
across SSDs, ±3× across CPU generations, and **dramatically** under
sustained multi-process load. Treat them as a sanity check, not a
contract.
