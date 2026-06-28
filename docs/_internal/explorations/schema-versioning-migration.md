# Schema versioning & migration — strategy

**Status:** execution diary for **task #5 (C3)** — design contract + progress log, shaped via
discussion (2026-06-27). The first sections record *why* `schema_version` matters and *how* a
breaking change migrates at scale; the **Execution diary** section at the bottom is the locked
contract for the 0.3.0 versioning reset and the source of truth for execution. Nothing is
built beyond the current write-only stamp yet; this is the path it grows along.

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
only the docs framing, not whether to stamp. **(Resolved 2026-06-27 — see Execution diary §3:
events *is* a published contract by virtue of the read/write API, so all four stores stamp and
all four get reference docs.)**

---

# Execution diary — C3 locked contract (2026-06-27)

This section is the contract. The sections above are the *why*; this is the *what we ship and
in what order*. Decisions below were settled interactively and are not to be re-litigated or
deviated from during execution (plan-is-contract). Anything genuinely missing is a STOP-and-
discuss, not an auto-fix.

## §0. The crux that drove every decision

Parquet (and Arrow, by the same "readable on every platform" logic) is **portable and
archivable** — so the moment a file leaves the daemon we stop owning every copy (customer NAS,
cold storage, a CAPA attachment). Two consequences fall straight out and fix the whole design:

1. **You only ever get the information a file captured at its version — forever.** A v1.0 file
   never gains a field a later version added. Migration normalizes *shape*, never recovers
   *information* (it NULL-fills what wasn't captured).
2. **Read-time projection is mandatory, not a choice.** Because you can't reach files in the
   wild, the read path must ingest any version you ever shipped. Rewriting files you *do* own
   is an optional optimization layered on top — never a substitute.

The derived layer (DuckDB index, materialized projections, query/UI) is the opposite: fully
rebuildable, always current-shape, zero forever-burden.

## §1. The locked contract

- **Coexist-always + optional-migrate.** Read-time projection is the floor; migrating owned
  files is opt-in (perf/housekeeping only). They are not alternatives — coexist is required,
  migrate is additive. (Supersedes the "v2/v3 coexist" framing above only in *labeling*: the
  strategy is identical; the version numbers reset — see §2.)
- **Dispatch happens exactly once**, at each store's *durable-artifact → rows* read boundary.
  Everything downstream of that point is single-version (current). The version seam is one
  place per store.
- **SemVer rule, identical for all four stores:**
  - **MINOR** (`1.0 → 1.1`) = additive only. `union_by_name` NULL-fills old files;
    `ALTER TABLE ADD COLUMN IF NOT EXISTS` extends the projection. **No adapter.**
  - **MAJOR** (`1.x → 2.0`) = breaking (rename / reshape / remove). **Needs a per-version
    adapter + a frozen reference doc for the outgoing major.**
- **Whitelist-dispatch reader.** The reader matches the stamp against *known* versions and
  refuses anything else with an actionable message:
  - present **and** whitelisted (`1.0`, later `1.1`, `2.0`…) → read via that version's adapter
  - present but **unknown** (a future `5.0`, or the abandoned runs `2.0`) → refuse: "unsupported
    schema version"
  - **absent** → refuse: "pre-1.0 / unversioned — regenerate". 1.0 is *always* stamped at write
    (part of this work), so absence can only mean a pre-stamp file. Concrete proof absence ≠
    1.0: old events files still carry `parent_path` inside their `StepStarted`/`StepEnded`
    JSON (dropped in #8) — provably pre-1.0 payloads. Treating absent as 1.0 would silently
    mis-parse them.

## §2. The 1.0 reset (the headline decision)

**Reset every store's schema to `1.0` at the 0.3.0 release; yank 0.2.0/0.2.1.** Rationale:

- The "support every shipped version forever" clock should start at a *designed* contract, not
  the accreted runs `2.0` shape (`SCHEMA_VERSION = "2.0"`, `schemas.py:37`) — a number that
  also *coincidentally tracks the 0.2.x package version*, its own confusion.
- `1.0` across all four stores at package `0.3.0` gives one clean baseline and **deliberately
  decouples schema version from package version** (schema `1.0` ≠ package `0.3.0` makes
  "independent version lines" unmistakable). Stores then diverge independently.
- The reshape work already committed (C4 `uut_serial_number`, C5 instruments `list<struct>`,
  #8 drop `parent_path`, #7 de-fuse) doesn't change — it just gets stamped `1.0` instead of the
  previously-planned `3.0`.

**So the pending "2.0 → 3.0 bump" reframes to: collapse to `1.0` + add the three missing
per-store stamps + regenerate `data/`.** There is **no adapter** for the 0.3.0 cutover —
pre-1.0 is unsupported *by design*, so old `2.0`/unstamped files are **regenerated**
(re-run the producers) and the daemon rebuilds its index from fresh 1.0 artifacts. The first
*real* adapter is written only when the first future MAJOR bump (`1.x → 2.0`) lands.

- **Package yank is outward-facing → needs the user's explicit go-ahead** (not done here). It
  rests on "no one holds real archived 0.2.x data," near-certain this early but the user's call.

## §3. The four stores — characterization & stamp locations (verified 2026-06-27)

Every store has the same two-tier shape: a **durable, portable artifact carries the stamp**;
a **derived warm index is rebuildable** and carries no version burden.

| Store | Durable artifact (the contract) | Derived/rebuildable | Stamp location | Version coords |
|---|---|---|---|---|
| **Runs** | parquet — content **fused** into the columnar schema | daemon DuckDB index + materialized tables | parquet footer metadata `schema_version` (already exists, `backends/parquet.py`) | **1** |
| **Events** | Arrow IPC — stable **envelope** (`_IPC_SCHEMA`, `event_log.py:40`) wrapping the `json` **payload** | warm event index (filter pushdown) | two Arrow file-metadata keys | **2** (envelope + event-catalog) |
| **Channels** | Arrow IPC — stable skeleton + a `value` column **typed by `value_type`** (`channels/models.py`, `sample_schema()`/`SCALAR_SCHEMA`) | warm channel index | Arrow file-metadata key | **1** |
| **Files** | blob + **sidecar** (`FileArtifactMetadata`, `files/models.py:10`) — blob is opaque user payload, sidecar is our metadata | warm files catalog (`store.py:194`: "sidecar is the durable truth a restart rebuilds from") | new `schema_version` field on the sidecar model | **1** |

Findings that shaped the per-store treatment:

- **Events is a contract on two levels** and *better*-positioned than parquet. Parquet fuses
  content+storage → one version line that bumps on every reshape. Events keeps content in the
  `json` blob (`event_store.py:264`, `model_dump(mode="json")`); the envelope columns are
  routing/ordering keys + *duplicated* typed projections of json values, so the envelope barely
  ever changes. New event types/fields go inside `json` (additive). Two coordinates:
  - **Envelope version** — the `_IPC_SCHEMA` storage shape. MAJOR only if `id`/`event_type`/the
    `(session_id, writer_key, event_offset)` ordering keys change semantics. Rare.
  - **Event-catalog version** — one constant beside the event models (`events.py`), e.g.
    `EVENT_CATALOG_VERSION = "1.0"`. **DECIDED: single catalog version; per-event-type versions
    deferred** ("add later if some event must break on its own cadence"). MAJOR only on a
    breaking payload change; new types/fields are MINOR.
  - Each events `.arrow` file therefore carries **two** metadata stamps and is self-describing
    on both axes.
- **Events is published** (read/written via API → the contents *are* a contract). The storage
  envelope is "just an arrow file" today — undocumented. C3 writes its **storage-format
  reference page** (envelope columns, the ordering contract, "json is truth / typed columns are
  projections") and cross-links the existing `event-types.md` (which already covers the payload).
- **Channels has a data-driven schema** — `sample_schema()` builds a per-channel schema from
  `value_type` (`scalar:float`, array, waveform, struct) over a stable skeleton (`received_at,
  sampled_at, value, source_method, session_id, sample_offset`). `value_type` self-describes the
  value shape, so a new shape is additive in the data → **one** envelope stamp, not two.
- **Files is mostly opaque payload** — the blob is the user's artifact (described by
  `mime`/`extension`, not our contract); our versioned contract is the **sidecar schema only**.
  Files migration rewrites only the sidecar and **never touches the blob**.

## §4. Re-index vs migrate — one adapter, two sinks

The whole versioning surface reduces to **one function per `(store, source-major) → current`**:
a pure **Arrow-table → Arrow-table** transform (Pydantic → Pydantic for the files sidecar)
emitting current-shape rows, composable across steps (`1.0→2.0→3.0` = chain). Everything else
is just where its output goes:

- **Re-index** (always; derived; blow-away-safe): `read durable → adapter(vN→current) → insert
  into DuckDB`. Old file stays on disk untouched. This is the daemon's existing scan path with
  the adapter slotted in, keyed on the file's stamp.
- **Migrate** (opt-in; owned files only): `read durable → adapter(vN→current) → write a NEW
  vCurrent durable file → atomic swap`. Same adapter, persisted output.

So **migration is "re-index that also persists the adapter output."** Consequences:

- **Adapter lives in Python at the read boundary, NOT a DuckDB view-per-version.** A view can
  only feed queries — it can't rewrite a parquet/arrow file or a sidecar, and the migrate sink
  needs exactly that. (Reshapes like parallel-arrays → `list<struct>` are also natural in Arrow,
  gnarly in SQL.) `union_by_name` still covers the additive/MINOR case for free; the Python
  adapter exists only for MAJOR steps.
- **Migration never recovers information** — purely structural (NULLs where a later major added
  fields). A migrated file is byte-different, information-identical. That's *why* it's optional:
  if the adapter is cheap, you may simply always project at read and never migrate.
- **The derived index is never migrated, only rebuilt.** Two distinct "versions" live here:
  durable artifacts (project-or-migrate, forever) vs the index's own internal table shapes
  (C2 column lists, materialized tables). When the *index* shape changes with new code, bump an
  index epoch, drop, and rescan through the adapters — never an in-place DB migration.

## §5. What 0.3.0 actually ships (the seam, not transforms)

1. **Stamp all four durable artifacts at `1.0`** (runs footer already; add events ×2, channels,
   files-sidecar). A small registry of version constants so "what versions exist" has one home.
2. **Whitelist-dispatch reader per store** (read stamp → known? → dispatch; else refuse with the
   §1 messages). Add a test asserting every freshly-written artifact carries its stamp.
3. **The adapter seam, registry shipping `1.0`-only (identity).** No speculative transform code
   — the first real adapter lands with the first future MAJOR bump.
4. **Regenerate `data/`** and rebuild the daemon index from the fresh 1.0 artifacts (pre-1.0
   unsupported by design; no cutover adapter).
5. **Per-store reference docs**: runs has `parquet-schema.md`; add the events storage-format
   page (§3) and per-store pages for channels + files, each frozen-per-major.

Out of scope / deferred: the package yank (needs go-ahead, §2); writing any `1.x → 2.0` adapter
(YAGNI until a real bump); the optional rewrite-compaction tool (strategy 2 above).

## §6. Open items

- [~] **Package yank 0.2.0/0.2.1** — **APPROVED 2026-06-27**, but **execution sequenced to the
      0.3.0 release** (it is a 0.3.0 release-checklist step, NOT a do-now action). Why the
      sequencing is mandatory, not just preferred: the remedy version must exist *before* the
      yank. Yanking today would (a) point `pip install litmus-test` *backward* to `0.1.3` (the
      highest non-yanked release) and (b) reference a 0.3.0 that isn't published. So: **publish
      0.3.0 first, then yank.** Mechanics when the time comes — PyPI yanks only via the web UI
      (no CLI/API path); the owner clicks each release at
      `https://pypi.org/manage/project/litmus-test/releases/`. Public reason string (final,
      becomes *true* once 0.3.0 is live): "Pre-1.0 on-disk data formats; abandoned by the 0.3.0
      schema-versioning reset (all stores restart at schema 1.0). Unsupported going forward —
      install 0.3.0 or later."
- [ ] Confirm no real archived 0.2.x data exists anywhere worth an adapter (assumed none).
- [ ] **Follow-on, not C3 — Query-API grain surface.** Decided (2026-06-27): **no `VectorQuery`**
      — `StepsQuery` is already vector-grained (`steps_materialized` PK `(run_id, step_path,
      step_retry, vector_index)`, `_runs_duckdb_daemon.py:237`; `StepRow` carries `vector_index`
      + per-vector `inputs`/`outputs`). Split only if a consumer ever needs step-*definition*
      grain and execution grain simultaneously. **No `OutputsQuery`/`InputsQuery`** —
      `_LANE_STRUCT` (name/value/unit/uut_pin) has no limits/outcome, unlike `_MEASUREMENT_STRUCT`
      (outcome/limits/characteristic_id/spec_ref), so there's no yield/Cpk surface to hang on
      them; their use is joined-to-measurement, already served on the vector row. If a real
      cross-run lane need appears, prefer a generalized lane query or a `lane=` param over new
      parallel classes (inputs/outputs share the identical `_LANE_STRUCT`).

## §7. Progress log

- **2026-06-27** — C3 contract shaped and locked via discussion; this diary written. No code yet.
  Predecessor reshapes already committed on `feat/0.3.0-at-rest-reshape`: C4, C5, #7, #8, #9, #10.
  C3 is **gated** only by the package-yank go-ahead (§6); the engineering (§5) is unblocked.
- **2026-06-27** — Package yank **approved**, but **execution sequenced to the 0.3.0 release**
  (the remedy version must exist first; yanking today would point installs back to 0.1.3 and
  cite an unpublished 0.3.0). Now a 0.3.0 release-checklist step, not a do-now action. Final
  public reason string authored (§6). The go-ahead *decision* gate is cleared — C3 engineering
  (§5) can start whenever; the yank itself waits for release day.
