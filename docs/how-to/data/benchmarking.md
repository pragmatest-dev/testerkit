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
Litmus benchmark — fast tier, 27 cases, 35.0 s
  Intel(R) Core(TM) i7 x24  |  15.34 GB RAM  |  Linux-6.6-x86_64

  Results — one row per case (operation x units x writers):
  operation             units  wrtrs    best ms    mean ms         throughput       RSS    CPU
  events.emit             100      1     19.183     20.604     5,213 events/s     396MB  1967%
  events.emit           1,000      1    146.721    151.925     6,816 events/s     470MB   278%
  events.emit           1,000      2    266.007    272.393     7,519 events/s   1,504MB   377%
  events.query          1,000      1     13.700     14.071    72,995 events/s     498MB   198%
  channels.write        1,000      1    432.763    434.345    2,311 samples/s     481MB   159%
  channels.write        1,000      2    458.778    462.298    4,359 samples/s   1,512MB  1934%
  runs.save                10      1    428.371    436.018          23 runs/s     936MB   178%
  ...

  Cost model (1 writer): fixed per-call overhead + marginal per-unit
  operation              overhead         per-unit
  events.emit            5.012 ms     141.71 µs/event
  channels.write         0.000 ms     434.72 µs/sample
  runs.save              0.000 ms   42945.01 µs/run
  runs.list              4.475 ms       4.14 µs/run
  ...

  Parallel scaling (writes), throughput by writers:
    channels.write     1w=2,311  2w=4,359  (1.89x at 2w)
    events.emit        1w=6,816  2w=7,519  (1.10x at 2w)

  Footprint (whole run): peak RSS 1541 MB  |  CPU 158% mean / 2397% max
```

## Read the numbers

**Each row is one real measurement** — not an average across sizes. An
operation appears once per unit count, and writes appear again per writer
count, so you can see how cost grows with both.

- **units** — how much work that row attempted: events emitted, samples
  written, runs in the index, files in the index, chunks streamed.
- **writers** — how many processes ran the write concurrently (1 for the
  size sweep; 2 and 4 for the concurrency rows).
- **best / mean (ms)** — wall time for that case. Best-of-N is the stable
  figure; a `mean` well above `best` flags a noisy case.
- **throughput** — records moved per second (`units ÷ best`, times
  writers). Writes measure the *durable* path — data on disk **and**
  indexed so a query sees it — so it's the rate a station actually
  sustains, not a buffered illusion.
- **RSS / CPU** — peak memory and CPU% sampled across the process tree
  *during that case* (the store's daemon does the work in a separate
  process). `—` when the case was too quick to sample. Needs `psutil`.

The **Cost model** summary fits each operation's best-time against units
(at 1 writer) into a **fixed overhead** (the per-call floor — RPC + query
plan + commit) and a **per-unit** marginal cost. A near-zero per-unit on
a query (e.g. `runs.list`) means the index size barely affects it — the
warm index is doing its job. **Parallel scaling** shows write throughput
by writer count and the speedup over one writer; near the writer count
means it parallelizes, near `1.0x` means a contention point.

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
