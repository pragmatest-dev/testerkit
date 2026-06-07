# Benchmark your machine

`litmus benchmark` measures how fast the four data stores — events,
runs, channels, files — run on *your* hardware, and writes a result file
you can send to the maintainers when you hit a performance problem.

It runs against a throwaway temporary directory, never your real data,
and cleans up after itself. Nothing is sent anywhere automatically.

This is a data-engine benchmark — it measures the throughput of specific
store operations (events written per second, channel samples per second,
queries per second), the way `fio` measures disk I/O or `sysbench`
measures a database. It is not a generic command timer.

## Run it

```bash
litmus benchmark
```

The fast tier takes about half a minute on a typical laptop. Each
operation runs at a sweep of **unit counts** and, for writes, a sweep of
**writer counts** — every combination is its own row. You'll see each
case tick past, then a results table and a summary:

```
Litmus benchmark — fast tier, 29 cases, 34.9 s
  Intel(R) Core(TM) i7 x24  |  15.34 GB RAM  |  Linux-6.6-x86_64

  Results — one row per case (operation x units x writers):
  operation             units  wrtrs    best ms         throughput     bytes/s      RSS    CPU
  events.emit           1,000      1    151.224     6,613 events/s           —    491MB   462%
  channels.write        1,000      1    413.882     2,416 samples/s   18.9 KB/s    650MB   158%
  channels.block       10,000      1      2.752 3,633,988 points/s   27.7 MB/s        —      —
  files.write              10      1     27.159    368 artifacts/s   36.0 MB/s  1,155MB    40%
  files.stream_raw         64      1     41.422    1,545 chunks/s   96.6 MB/s  1,025MB   158%
  runs.save                10      1    426.680          23 runs/s           —    983MB   178%
  ...

  Cost model (1 writer): measured per-call floor + marginal per unit
  operation                     floor   marginal (rec/s)        (time)   (bytes/s)
  events.emit                8.18ms@1            6,984/s 143.19µs/event           —
  channels.write             0.41ms@1            2,416/s 413.89µs/sample   18.9 KB/s
  channels.block         0.67ms@1,000        4,314,250/s  0.23µs/point   32.9 MB/s
  runs.save                 46.15ms@1               24/s 42280.89µs/run           —
  ...

  Parallel scaling (writes), throughput by writers:
    channels.write     1w=2,416  2w=4,407  (1.82x at 2w)
    events.emit        1w=6,613  2w=7,677  (1.16x at 2w)

  Footprint (whole run): peak RSS 1541 MB  |  CPU 162% mean / 2261% max
```

## Read the numbers

**Each row is one real measurement** — not an average across sizes. An
operation appears once per unit count, and writes appear again per writer
count, so you can see how cost grows with both.

- **units** — how much work that row attempted: events emitted, samples
  written, runs in the index, files in the index, chunks streamed.
- **writers** — how many processes ran the write concurrently (1 for the
  size sweep; 2 and 4 for the concurrency rows).
- **best (ms)** — wall time for that case. Best-of-N is the stable figure.
- **throughput** — records moved per second (`units ÷ best`, times
  writers). Writes measure the *durable* path — data on disk **and**
  indexed so a query sees it — so it's the rate a station actually
  sustains, not a buffered illusion.
- **bytes/s** — throughput in bytes for the byte-sized operations
  (channel samples, file blobs/chunks). For high-rate acquisition this is
  the number that matters; `—` for ops whose records aren't a fixed size
  (events, runs, queries).
- **RSS / CPU** — peak memory and CPU% sampled across the process tree
  *during that case* (the store's daemon does the work in a separate
  process). `—` when the case was too quick to sample. Needs `psutil`.

The **Cost model** summary splits each operation (at 1 writer) into a
**measured per-call floor** (the best time at the smallest scale — the
fixed RPC + plan + commit cost, a real measurement, not an extrapolation)
and a **marginal** cost of one more record, shown three ways: **records/s,
time, and bytes/s**. A near-zero marginal on a query (e.g. `runs.list`)
means index size barely affects it — the warm index is doing its job.
**Parallel scaling** shows write throughput by writer count and the
speedup over one writer; near the writer count means it parallelizes,
near `1.0x` means a contention point.

**One thing the report makes obvious:** for high-rate channel data, write
a **block** (an array/waveform per call, `channels.block`) rather than a
sample at a time (`channels.write`) — one RPC carries the whole block, so
block writes hit tens of MB/s while scalar-per-sample writes are bound by
the per-call RPC. Bulk acquisition belongs in blocks or file streams.

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--full` | off | Full sweep: units 100 / 1k / 10k and writers 1 / 2 / 4. A few minutes. |
| `--rounds N` | `3` (fast), `5` (full) | Timed rounds per case. |
| `-o, --output DIR` | `.benchmarks` | Where the result folder is written. |
| `--no-save` | off | Print the summary only; write no folder. |

## Where the result lands

Each run writes a dated folder, `.benchmarks/<date>/`, with two files:

- **`report.md`** — the human deliverable. Paste it into a GitHub issue
  and the tables render natively.
- **`report.json`** — the same data, machine-readable, for trend
  comparison across runs.

Both are self-describing — they record your hardware, the library
versions, exactly which options you ran, every number, and the run's
RAM/CPU footprint, so a maintainer can read either one cold. A second
run on the same day gets a time-suffixed folder so nothing is clobbered.

`.benchmarks/` is git-ignored, so these stay local until you choose to
send one.

## Richer capture (optional)

The RAM and CPU footprint needs `psutil`. Install the extra:

```bash
pip install "litmus-test[benchmark]"
```

Without it, the benchmark still runs and reports throughput; the
footprint line is omitted with a note.

## Reporting a performance problem

1. Run `litmus benchmark` (or `litmus benchmark --full` for the full
   picture).
2. Paste `.benchmarks/<date>/report.md` into your issue (it renders as
   tables), or attach `report.json`.

Because the report carries your hardware and versions, the maintainers
can compare it against reference numbers and tell whether you're hitting
a known limit or something specific to your machine.

## See also

- [Reference → Performance limits](../../reference/data/performance-limits.md) — reference numbers the benchmark feeds
- [Reference → CLI](../../reference/cli.md) — full `litmus benchmark` flag list
- [Concepts → Data](../../concepts/data/index.md) — the four stores being measured
