# Benchmark your machine

`litmus benchmark` measures how the four data stores — events, runs, channels,
files — perform on *your* hardware, then turns those measurements into plain
capacity answers: how many test runs you can record in parallel, how many
instrument channels you can log at a given rate, how much you can store before
the disk fills, and how much of your machine the data layer uses while it works.

It runs against a throwaway temporary directory, never your real data, and
cleans up after itself. Nothing is sent anywhere automatically.

## Run it

```bash
litmus benchmark
```

The fast tier takes about half a minute on a typical laptop. Each case ticks
past, then you get a capacity report:

```
Litmus performance on this machine

  Intel(R) Core(TM) Ultra 9 275HX · 24 cores · 15.34 GB RAM · Linux 6.6.87 (WSL2) · litmus 0.2.0 · duckdb 1.5.0 · pyarrow 23.0.0 · fast tier · 35s

  Recording a production test run costs ~74 ms and ~0.3 MB. This machine
  finalizes ~15 runs/s (≈153 parts in parallel at a 10s cycle) and can hold
  ~3.42M runs. Litmus stays out of your test's way.
  Under load it uses ~<0.1% of this machine's CPU and ~1.3 GB (9% of RAM) —
  the rest stays free for your test code and other apps.

  Recording test runs (by phase):
    phase              time/run    on disk     runs fit
    characterization      2.6 s    156.8MB        5.69k
    validation           229 ms      5.1MB         177k
    production            74 ms      0.3MB        3.42M

  Per-operation rates:
    operation                    latency   sustained rate
    Log a measurement            0.25 ms          3.97k/s
    Write a waveform block       0.16 ms   9.98M points/s
    ...
```

## Read the numbers

The machine line is first, so the report is interpretable cold — every number
below it is "measured on *this* machine," not a guarantee.

- **The verdict** is computed from this run, not hardcoded. It states what
  recording one production run costs, how many runs/s the machine finalizes
  (and the parts-in-parallel that implies at a stated test cycle), and how
  many runs fit before the disk fills.
- **Footprint under load** is the Task-Manager view: the share of your machine's
  CPU and RAM the store services use while working — so you know how much is
  left for your test code and other apps. The data services are memory-resident
  and mostly idle-waiting, so RAM, not CPU, is usually the footprint that grows.
- **Recording test runs (by phase)** shows three representative test phases —
  characterization (raw-heavy), validation (corners), production (lean) — with
  the time and on-disk size per run and how many fit on your free disk. The
  compositions are illustrative and tunable; the costs are extrapolated from the
  per-component coefficients this run measured.
- **Capturing instrument data** answers the channels question in datasheet
  terms: how many channels you can log at 1 kS/s and 10 kS/s, the ingest
  ceiling, and how long a given capture fits on disk — compare it to your own
  instrument's capture rate.
- **Per-operation rates** are the underlying measurements: the latency of one
  call and the sustained rate, per store operation.

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--full` | off | Full sweep: more sizes and writer counts (1 / 2 / 4). A few minutes. |
| `--rounds N` | `3` (fast), `5` (full) | Timed rounds per case (best-of-N is reported). |
| `-o, --output DIR` | `.benchmarks` | Where the result folder is written. |
| `--no-save` | off | Print the summary only; write no folder. |

## Where the result lands

Each run writes a dated folder, `.benchmarks/<date>/`, with two files:

- **`report.md`** — the human deliverable: the report above, as tables. Paste it
  into a GitHub issue and it renders.
- **`report.json`** — the same run, machine-readable. Alongside every per-case
  number it carries the **coefficient block** (per-component cost, on-disk bytes,
  concurrency sweep, footprint) — the data behind the capacity figures, so a
  scenario can be recomputed for any composition.

Both record your hardware, the library versions, and exactly which options you
ran, so a maintainer can read either one cold. A second run on the same day gets
a time-suffixed folder so nothing is clobbered. `.benchmarks/` is git-ignored,
so these stay local until you choose to send one.

## Richer capture (optional)

The CPU/RAM footprint needs `psutil`. Install the extra:

```bash
pip install "litmus-test[benchmark]"
```

Without it, the benchmark still runs and reports every rate and capacity; the
footprint line is omitted.

## Reporting a performance problem

1. Run `litmus benchmark` (or `litmus benchmark --full` for the full picture).
2. Paste `.benchmarks/<date>/report.md` into your issue, or attach `report.json`.

Because the report carries your hardware and versions, the maintainers can
compare it against the reference numbers and tell whether you're hitting a known
limit or something specific to your machine.

## See also

- [Reference → Performance limits](../../reference/data/performance-limits.md) — reference numbers the benchmark feeds
- [Reference → CLI](../../reference/cli.md) — full `litmus benchmark` flag list
- [Concepts → Data](../../concepts/data/index.md) — the four stores being measured
```
