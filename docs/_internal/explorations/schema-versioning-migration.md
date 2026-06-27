# Schema versioning & migration — strategy

**Status:** design note (2026-06-27), shaped via discussion. Records *why* `schema_version`
matters and *how* a breaking schema change is meant to migrate at scale. Nothing here is
built beyond the current write-only stamp; this is the path it should grow along.

## Current state (verified 2026-06-27)

- `schema_version` (parquet file-level metadata, `SCHEMA_VERSION` in `schemas.py`) is
  **write-only** — nothing reads it to branch or migrate. (The one `schema_version` read in
  the tree, `capability.py:17`, is a comment about a hypothetical future catalog migration
  tool, not code.)
- **Additive** schema change is handled name-based, no version check:
  - reads: `union_by_name=true` (old files null-fill a missing column)
  - DuckDB projections: `ALTER TABLE ADD COLUMN IF NOT EXISTS`, driven from the
    `_*_PERSISTED_COLUMNS` tuples (`_duckdb_daemon.py`, files catalog, …)
- **Breaking** change today = **regen**: `rm -rf data/` and re-run. Stated outright in
  `channels/models.py:48` ("no-backcompat; `rm -rf data/` is the migration"). Fine
  pre-release; see below for why it doesn't survive contact with real data.
- DuckDB has the DDL **primitives** (`ALTER … ADD/DROP/RENAME COLUMN`, `ALTER COLUMN … SET
  DATA TYPE`) but **no versioned migration runner** (no Alembic/Flyway equivalent). And the
  DuckDB DB is a **derived projection** — for anything `ALTER ADD` can't do, the answer is
  *drop the DB and rebuild from parquet*, never an in-place DB migration. So the migration
  concern lives one layer down, at the **parquet source**.

## Why regen doesn't scale

Regen is O(all runs) — and for a hardware test platform it's frequently **not even
possible**: the DUT shipped, the bench was reconfigured, the conditions are gone. Re-running
50k physical runs to change a column shape isn't slow, it's a non-option. Regen is a
pre-release crutch *because* pre-release there's nothing real to lose.

## The three strategies (regen is the worst)

1. **Regen** — re-run everything. O(all tests); usually impossible for hardware.
2. **Rewrite** — offline script reads every old parquet, writes it in the new shape.
   O(N files), once per breaking change. Doable, but touches every file; slow at millions.
3. **Read-time adapt, keyed by `schema_version`** — leave old files **untouched on disk**;
   the ingest layer reads each file's version and projects old-shape and new-shape files
   into the **same** unified projection. No rewrite, no regen. The cost is paid once at
   ingest (which the daemon already does) and amortized.

(3) is the one that scales, and it's what makes `schema_version` **load-bearing rather than
cruft.** The daemon already reads every parquet once to build the projection and does
additive reconciliation (`union_by_name` + `ALTER ADD`). Read-time adaptation is that same
seam extended: *read `schema_version`, apply the matching projection.* v2 files project the
old way, v3 files the new way, they coexist. Without the stamp you can't tell which transform
to apply → forced back to rewrite-or-regen.

## On-path implication (aspirational = stay on path)

The scale argument *is* the argument for taking `schema_version` seriously now: **stamp it,
and structure ingest to branch on it — even while it's a single-version no-op today** — so
read-time migration slots in later instead of becoming a forced rewrite. Shipping regen-only
is the off-path throwaway; a version-tagged, adapt-at-ingest projection is the abstraction the
future slots into. (Same trap class as FileStore's bespoke local I/O vs object-store-shaped.)

## Tradeoffs to own

- **Read-time adapters accumulate** — one per version transition, kept forever for the old
  files that still exist. That's the price of never rewriting.
- **Escape hatch:** a periodic *optional* rewrite-compaction (strategy 2) retires old
  adapters once old files age out. Operator choice, not a forced migration.
- **Some changes are genuinely lossy** and can't be adapted forward. The de-fuse is the
  example: you can't un-fuse old v2 files — the rerun rows were never written, the data is
  absent. So v2 files keep their fused shape + `retry_count`; only v3-onward get honest
  per-execution rows. **Mixed-version coexistence, adapted at read, is the scalable truth** —
  not a single uniform shape.

## Consequence for C3 (per-store version stamps)

C3 is **not** "stamp cruft." Under the scale lens it's "**stamp the migration key + make
ingest version-aware.**" The version earns its keep two ways: as a portability signal for
external/lakehouse consumers, *and* as the internal key that lets ingest adapt old files
forward without rewriting them. The open precondition still stands — decide whether
events/channels/files on-disk are a published external contract — but either answer keeps the
stamp: external consumers need it, and internal read-time migration needs it. What changes is
only the docs framing, not whether to stamp.
