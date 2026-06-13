# Handoff — `write_many` / batch-native channel producer

> Written at the end of a long, exploratory session. **The work below is
> unproven and was authored while the measuring machine was thermally
> throttling — evaluate it with fresh eyes.** Branch: `spike/batch-native-channels`
> (off `feat/0.2.0-data-improvements`).

## The question to answer first

**What is the right signature for `write_many`, decided by measurement — not taste?**

A batch write exists to escape the per-*call* tax of `write()`. The catch: its
speed comes from **columnar building** (build N values into Arrow columns with
zero per-element Python). Three candidate shapes, and they trade ergonomics
against that speed:

1. `write_many(ch, values, timestamps)` — two **parallel arrays** (ideally numpy).
   Fastest (pure columnar), but two lists the caller must keep aligned.
2. `write_many(ch, [(t, v), …])` — **list of pairs**. Ergonomic, but needs
   per-element unzip on the hot path.
3. `write_many(ch, [Sample(t, v), …])` — **list of objects**. Most readable,
   slowest (attribute access × N).

The user's framing, which is correct: each sample has its **own timestamp**, so
the input is *timestamped samples*, not bare values — bare `[v1, v2]` forces a
shared `received_at` and collapses back toward an array-write. The per-sample
time belongs in `sampled_at` (hardware/source instant); `received_at` can stay
the batch-arrival time.

**Next step:** benchmark all three shapes (cooled machine, official harness) and
pick the signature on data. Hypothesis: parallel arrays win big; pairs are
acceptable sugar that unzip internally; objects are too slow to be the primary
form. **Do not assert — measure.**

Related framing the user wants to preserve — `write_many(ch, [v…])` is "a write
in **array form**" (N rows, N sequences, individually addressable), which is the
*opposite* of `write(ch, [v…])` ("array write" = one waveform sample, 1 row,
uniform `sample_interval`). Same-looking call, opposite meaning.

## Measurement hygiene (this session got burned)

- **The machine throttled ~4× mid-session.** The identical harness that read
  86k samp/s early read **21k** later (`measure_truth.py`, serve=False, same
  code). **Absolute numbers across time are not comparable.** Only **same-run,
  back-to-back ratios** are trustworthy.
- For real absolutes, use the official `litmus benchmark` (warmup + min_rounds +
  gc-disable — built for this) on a **cooled** machine. Don't quote `.tmp/`
  one-shot absolutes as proof.

## What is actually known (verified this session)

- The channel **producer's per-sample ceiling is per-*call* overhead**, not
  disk or DuckDB: cProfile showed `make_channel_uri` (urlencode **per write**),
  `_to_arrow_row` (dict build per write), and the push enqueue dominating.
- **`write()` is flat in N** (~21k throttled), *not* degrading with file count —
  the per-flush-rotation file-storm hypothesis was tested and **disproven**.
- **`write_many` durable path is ~200× `write()` same-run** (correctness
  verified: 5 in → 5 out, values + `sequence` exact). The isolated ceiling
  experiment (`exp_writemany.py`) hit ~4.5M samp/s columnar; the real path
  (`bench_writemany_real.py`) tracked it for the durable write.
- **With the daemon, end-to-end is only ~1.7×** (not 200×), because write_many's
  push is **synchronous** and the daemon **ingests per-row**. The durable write
  is fixed; the live/query path is the next ceiling.
- **The per-sample push (`_push_loop` → `_flight_push`) is the original
  bottleneck and is NOT batched** — one 1-row batch / one gRPC message per
  sample. The drain-coalesce that Phase 2 added to the *subscriber* side was
  never added to the *producer push* side.
- **Gating the push on "has subscribers" is the WRONG fix** — verified the index
  is fed *only* by the runtime push (`_scan_disk` runs once at startup,
  `store.py:842`; no runtime segment scan). Gating it would make `query()` miss
  everything written this session until restart. The right fix is a **per-flush
  coalesced push** (push the same batch that flushes to the segment, drain-
  coalesced) — keeps the index current cheaply, no consistency hole, reconciled
  by the `sequence` column.

## What is built on this branch (WIP, unproven)

- `src/litmus/data/_ipc_writer.py` — `BufferedIPCWriter.append_batch(batch)`:
  bulk path, drains the dict buffer then writes a pre-built batch, fires
  `_on_flush`.
- `src/litmus/data/channels/store.py`:
  - `_flight_push` refactored to delegate to **`_flight_push_batch(channel_id,
    batch)`** (the batched-transport core — one `write_batch` for the whole
    batch on the held stream).
  - **`write_many(channel_id, values, *, sampled_ats=…, …)`** — scalar-only
    columnar producer: builds one durable batch + one wire batch, `append_batch`
    + `_flight_push_batch`. **Push is synchronous and the API takes bare
    `values` + optional parallel `sampled_ats` — that signature is exactly what
    the open question above must settle.**
  - added `sample_schema` to the models import.
- No public `channels.write_many` wrapper, no tests, no docs yet.
- Experiments live in `.tmp/` (gitignored, on disk): `measure_truth.py`,
  `measure_ceiling.py`, `exp_gate.py`, `exp_push_batch.py`, `exp_block.py`,
  `exp_writemany.py`, `bench_writemany_real.py`.

## Remaining deliverables the user named (all still open)

1. Decide the `write_many` signature **by benchmark** (the question above).
2. **Prove it by benchmark** on the real path, cooled machine / official harness.
3. **Show it by example.**
4. **Document when to use what** — the tier ladder: `write` (single, simplest,
   per-point) / `write_many` (batch, per-point timestamps, you supply the chunk)
   / array-block (uniform interval, no per-point time) / raw file-stream (bytes,
   disk-bound) — each with its liveness/granularity trade-off.
5. The daemon end-to-end: decide whether to make it fast (async batched push +
   columnar daemon ingest) or accept write_many as a durable-bulk tool with the
   daemon as the slower live/query path.

## Wider context (the parent effort)

This sits inside "maximize each store's local perf within the swap-clean
requirements." Channels' per-sample path is the soft ceiling (the rest is
physics). Still untouched: **FileStore** streaming (53 MB/s vs 1.3 GB/s
blob-write — same per-chunk-push disease) and **EventStore** (JSON-payload
encode/parse). **RunStore is already adequate** (verified). See
`channels-write-scaling.md` and `data-store-backends.md`.
