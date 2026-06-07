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

The fast tier takes well under a minute on a typical laptop. You'll see
each workload tick past as it runs, then a summary:

```
Litmus benchmark — fast tier, concurrency 2, 5 rounds
  Intel(R) Core(TM) i7 ×24  |  15.34 GB RAM  |  Linux-6.6-x86_64
  12 workloads · 5 timed rounds each · finished in 36.2 s

  workload                n/call  best (ms)  mean (ms)   per unit       throughput
  events.emit                300   1279.443   1295.315   4.265 ms     234 events/s
  events.query                 1     13.611     15.222  13.611 ms      73 queries/s
  runs.save                    1     39.374     41.443  39.374 ms        25 runs/s
  channels.write             500    212.018    216.481   424.0 µs   2,358 samples/s
  files.write                  1      3.093     14.093   3.093 ms     323 artifacts/s
  ...

  Parallel writers — aggregate ops/s by writer count:
  store               1w          2w   speedup
  events             215         347     1.62×
  channels         2,140       4,235     1.98×
  files              380         579     1.53×
  runs                21          23     1.11×
    each writer writes: 300 events, 500 channels, 100 files, 15 runs

  Footprint: peak RSS 1,414 MB  |  CPU 0–1399% (mean 148%)
```

## Read the numbers

The line under the machine fingerprint says how much work ran and how
long it took — e.g. `12 workloads · 5 timed rounds each · finished in
36.2 s`. Then the per-workload table:

- **throughput** — the headline. Every write workload measures the
  *durable* path: data written to disk **and** indexed so a query can
  see it. That's the rate a test station actually sustains, not a
  buffered illusion. Query workloads report queries per second.
- **n/call** — how many units one timed round does (300 events, 500
  samples, 1 query). Throughput is `n/call ÷ best`.
- **best / mean (ms)** — per-round wall time. Best-of-N is the stable
  number (it sheds scheduler hiccups); a `mean` far above `best` means a
  noisy workload.
- **per unit** — the cost of a *single* event / sample / query (best ÷
  n/call). This is the per-sample timing — 4.3 ms to durably write one
  event, 424 µs per channel sample.
- **Parallel writers** — the same write workload run by 1, then N
  processes at once (default N=2). The table pivots so you read scaling
  left-to-right; **speedup** is the N-writer rate over the 1-writer
  baseline. Near the writer count means it parallelizes; near `1.00×`
  means you've found a contention point.
- **Footprint** — peak RAM and CPU% across the whole run, including the
  background daemons that do the indexing. Use it to answer "did this
  fit in memory / saturate a core?" (Requires the `psutil` extra — see
  below.)

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--full` | off | Runs the scale sweep and a 1/2/4 concurrency sweep. Several minutes. |
| `--concurrency N` | `2` | Parallel writers in the fast-tier probe. Most setups never run four stations at once; bump it if yours does. |
| `--rounds N` | `5` | Timed rounds per workload. |
| `-o, --output DIR` | `.benchmarks` | Where the result JSON is written. |
| `--no-save` | off | Print the summary only; write no file. |

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
