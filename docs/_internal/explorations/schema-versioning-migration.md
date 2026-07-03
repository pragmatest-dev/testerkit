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

## §5.1 Verification — how C3 gets tested

The catch: C3 ships a *version-aware* seam whose only version is `1.0` and whose only adapter is
identity — so the seam's real value (project an old MAJOR forward) can't be exercised by
production code until the first `2.0`. Testing therefore splits into "directly testable now" and
"de-risk the dormant path with synthetic inputs." The bare stamp-round-trip the earlier plan
listed is only the first of these.

**Directly testable now (the bulk of §5):**

- **Stamp round-trip, per store.** Write via each store → read the stamp back → assert `1.0`.
  Events asserts *both* coordinates (envelope + event-catalog).
- **Whitelist-dispatch reader — all three branches**, via a doctored-stamp fixture (tamper or
  strip the metadata key on a written artifact):
  - `1.0` → reads
  - synthetic unknown (`2.0`/`5.0`) → refuses, "unsupported schema version"
  - absent → refuses, "pre-1.0 / regenerate"
  This tests the dispatch logic with no second real version needed.
- **No-unstamped-write convention** — a `tests/test_conventions.py`-style guard that fails if any
  durable write path ships without a stamp, so a future store can't silently regress to
  unversioned (which the reader would then refuse at read time).

**De-risk the dormant adapter path (otherwise untested until 2.0):**

- **Test-only synthetic adapter.** Register a throwaway non-identity transform *in the suite only*
  (e.g. a fake column rename, `0.9→1.0` or `1.0→2.0`), feed it a doctored-stamp fixture, and
  assert both sinks end-to-end: **re-index** (old-stamp fixture → daemon index holds current-shape
  rows) and **migrate** (old-stamp fixture → adapter → new file is stamped current, content
  transformed, atomic-swapped). This is the highest-value C3 test — it exercises the exact
  machinery that is otherwise dormant until a real bump.
- **Frozen golden 1.0 corpus, committed now.** One small golden file per store at the 1.0 shape,
  checked in. Two payoffs: pins the 1.0 on-disk shape against accidental drift today, and
  *becomes the regression input* the real `1.0→2.0` adapter is tested against later. This is what
  turns "support 1.0 forever" from a promise into a passing forward-migration test.

**Honest caveat:** until a second *real* version exists, this tests the machinery with synthetic
inputs, not a real migration. The first production adapter at `2.0` is still where it meets real
old data — but the golden corpus shrinks that risk to "we have a real frozen 1.0 file and a
passing forward-migration test," not "we find out at 2.0."

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
- **2026-06-27** — Added §5.1 Verification: stamp round-trip per store, three-branch
  whitelist-reader test (known/unknown/absent via doctored fixtures), no-unstamped-write
  convention, a test-only synthetic adapter exercising both re-index + migrate sinks, and a
  committed frozen golden 1.0 corpus as the future `1.0→2.0` adapter's regression input.

## §8. Lifecycle coordination — verified findings (2026-07-02)

The forward-only-adapter design rests on **local clients are never at a newer version than
the singleton daemon.** Verified against `_daemon_lifecycle.py`:

- **The invariant HOLDS at `acquire()` time.** `acquire()` (`:129–164`), under a file lock,
  compares the running daemon's `litmus_version` to the client's; older daemon → kill
  (`_kill_daemon` SIGTERM→2s→SIGKILL, `:184`) + respawn the client's version; newer-or-equal →
  attach. An unversioned legacy daemon is treated as `0.0.0` → always upgraded. So after any
  client acquires, **daemon ≥ the acquiring client** — but **NOT** necessarily the machine's
  newest (CORRECTED 2026-07-02). An old repo cannot launch a newer daemon (it only has old code),
  so an old daemon runs indefinitely whenever an old repo is the active one. It therefore *does*
  encounter newer files and cannot backward-adapt them → it **defers** them (its own ingestion
  skips them). It is saved from total blindness only by the **shared** index: when a newer daemon
  last ran and upgraded the shared dir, it already ingested those newer files into `_index.duckdb`,
  so an older *same-major* daemon reading that shared index still sees the rows at its own
  projection (**piggyback**). Two failure modes remain: a **non-additive** shared-index change
  crashes the old daemon at view-creation (#47); a **newer-major** file is genuinely unreadable by
  old code (correct-but-incomplete). The epoch-per-major index (§9, #47) keeps same-major daemons
  sharing one index (preserving the piggyback) and forks only on a major boundary (where isolation
  is mandatory anyway). **Floor:** an old daemon can never fully show a newer major's data — the
  achievable goal is safe (#47) + honest ("N newer artifacts hidden; upgrade") + preserved.
- **Caveat — it compares PACKAGE version, not schema.** The guarantee we actually need holds
  only if schema versions never *decrease* as package version rises (monotonic). Discipline +
  guard needed (task #41).
- **No wait-state; the kill ignores active refs.** A newer client kills the old daemon even
  while an older client is mid-test-execution (refs are consulted only by the idle-reaper
  `monitor_refs`, not the upgrade path). **Impact is bounded:** the running client's test data
  is written to disk *before* the daemon notify, `notify_new_run` swallows all errors and never
  raises, and the respawned daemon re-ingests on-disk parquets via its startup `rglob` scan —
  so no test error, no data loss. What blips: live queries/streams (which self-heal via the
  query path's reacquire+retry) and the daemon's in-memory inflight projection (rebuilds).
- **Two refinements parked as tasks:** #41 schema-capability-gated upgrade (only kill on a MAJOR
  bump the running daemon can't read — advertise `KNOWN_SCHEMA_VERSIONS` in the state file, so
  MINOR-newer clients just reuse the daemon); #42 the notify path only `reset()`s the pooled
  client without re-resolving location, so post-respawn writes from a pure-writer client sit
  un-indexed until the next query/restart — make notify reacquire-on-failure like the query
  path.

## §9. The coexistence model — three layers (2026-07-02)

**The requirement.** One machine, N repos each pinned to a different Litmus version, **one shared
global data dir + one singleton daemon per store** (deliberate — it's what lets cross-project data
be queried together, like remote machines against a central server). Any client can write; any
daemon can be the one running. **All of it must coexist.**

**The organizing principle.** Every object splits into two layers, versioned differently:

| | **Durable layer** — the files | **Derived layer** — the daemon's index |
|---|---|---|
| Role | source of truth | rebuildable cache |
| Sharing | **shared** across all versions | **per-version**, never shared across incompatible ones |
| Lifetime | forever | disposable |
| Versioning | self-describing stamp + forward adapters | epoch-keyed, rebuilt from the durable layer |

Every version-coexistence bug found in this session is the same defect: **a layer wasn't
version-aware yet.** There are only **three** layers, so this is finite:

1. **Durable files (shared, versioned) — BUILT (V1–V4 + #43).** Each file self-describes via its
   stamp; any daemon reads forward through adapters; a file too new for today's daemon is
   *deferred, not lost* (#43), and healed by a newer one. This is where "all data lives together"
   literally happens — a v1 file and a v9 file sit in one store, and any daemon projects both into
   one answer. **In harmony already.**
2. **Derived index (per-version, rebuildable) — DESIGNED (#47), unbuilt.** See below.
3. **The wire (client ↔ daemon) — DESIGNED (#44), unbuilt.** The `db\0table` / `db\0SQL` Flight
   envelope + the JSON API are unversioned. Backward-compatible responses + a version byte /
   capabilities handshake here let old clients ride the newest daemon (so they never spawn an old
   one); #47 is the safety net for when they do.

**The index-epoch design (corrected).** The persistent `_index.duckdb` is TODAY one shared file,
additive-`_ensure_schema`, no epoch (`_runs_duckdb_daemon.py:1935`) — which crashes an old daemon
that opens a **non-additively** newer index (#47). Fix: **epoch the index filename**
(`_index.e{N}.duckdb`), where **N tracks the index's structural MAJOR shape — not the package or
schema version.** Consequences:

- **The epoch does nothing within a major.** Minor/additive index changes keep sharing one file,
  incremental, O(new-files) startup — exactly today. So the count of index files = number of
  distinct index MAJORS ever run on the machine ≈ a handful, *not* one per release.
- **Rebuild is once-per-major-per-machine, not constant.** A new-major daemon builds its epoch
  file once (a full forward-adapting scan of the durable files), then persists + goes incremental.
  Old-major daemons keep their own persisted epoch file, never rebuilt. Flapping between two majors
  = each epoch persists and catches up on the small delta since *it* last ran — not a rebuild.
- **A daemon opens ONLY its own epoch's file**, so it never opens an incompatible DB — the #47
  crash is structurally impossible, not merely caught.
- **This is strictly better than Postgres/Lucene** (§10): their index *is* the truth, so they must
  *refuse* (Postgres catversion) or *reindex-in-place* (Lucene N-1); ours is a cache over the
  durable files, so we rebuild per epoch instead. The single-file + version-stamp-inside +
  rebuild-on-mismatch alternative (the SQLite `user_version` shape) is the *constant-rebuild*
  design under version flapping — per-epoch **files** avoid it.
- **No aggressive GC.** Keep any major the machine still runs (small files); reap only a
  truly-dead major, optionally. (An earlier "GC older than current−1" idea was wrong — it
  reintroduced rebuild-thrash.)
- **Guard:** a CI test that fails if the index DDL changes without an `_INDEX_EPOCH` bump (extend
  the existing steps CREATE↔migration-tuple drift guard, commit `11478d9c`) — the one failure mode
  of a manual epoch is forgetting to bump it.

**Why `major.minor` is load-bearing here.** MINOR = additive → handled by `union_by_name`, **no
adapter, no epoch**. MAJOR = breaking → **one** adapter + **one** epoch. So adapters *and* index
epochs both track MAJORS only — which is exactly what bounds them to a handful. The version scheme
is what makes the coexistence tractable.

## §10. Prior art — this design is assembled from established practice (2026-07-02)

Researched 2026-07-02 (web). The design is the union of five well-trodden bodies of work; the two
unbuilt pieces (#47, #44) each have a canonical blueprint.

**The two axes** come from Confluent Schema Registry's vocabulary: **BACKWARD** (new code reads old
data), **FORWARD** (old code reads new data), **FULL**, and **TRANSITIVE** (checked against *every*
prior version). Our "newest daemon reads every file ever" = **BACKWARD_TRANSITIVE** (the strongest).
We *avoided* needing FORWARD by making the daemon always ≥ the client (the lifecycle invariant) and
handling the residual (old daemon, new file) by **deferral** (#43) instead of forcing old code to
read new data.

| Litmus mechanism | Prior art | Verdict |
|---|---|---|
| Read-time adapt (dispatch + adapters) | Avro reader/writer schema resolution; event-sourcing **upcasting** (Young/Axon) | canonical |
| Additive `union_by_name` / nullable `ALTER ADD` | Avro "match by name, default the additions" | textbook |
| `major.minor`: MINOR additive, MAJOR needs adapter | SemVer for data; **Arrow/Parquet** format versioning | same model as our file format |
| Refuse/defer an unknown version | Arrow: read it **or detect that you cannot** | matches |
| Support every version forever (TRANSITIVE) | Event sourcing ("readable years later"); *vs* Lucene N-1 | deliberate, domain-justified (costlier) |
| daemon ≥ client ⇒ only need BACKWARD | Confluent: "backward is the natural axis" | elegant reduction |
| Keep schema `1.0`, not `0.1` | SemVer + Arrow: **pre-1.0 = *no* guarantees** | `0.1` contradicts the scheme |
| Index epoch, rebuilt-from-files (#47) | Postgres **catalog version** (refuse); Lucene index-version (N-1 + reindex) | *better* — we rebuild; they can't |
| Wire versioning (#44) | **Kafka** magic-byte + ApiVersions handshake; Protobuf stable field numbers | unbuilt; blueprint proven |
| Migrate / compaction (contract phase) | Fowler **Parallel Change** (expand → migrate → contract) | shape matches |

Key sources: Kleppmann, *Schema evolution in Avro/Protobuf/Thrift*
(martin.kleppmann.com/2012/12/05); Apache Avro spec (avro.apache.org/docs); Confluent, *Schema
Evolution & Compatibility Types* (docs.confluent.io); Axon, *Event Versioning*
(docs.axoniq.io) + InfoQ, *Versioning in Event Sourced Systems*; pgPedia, *catalog_version_number*
(pgpedia.info); Apache Lucene upgrade-policy (github.com/apache/lucene/issues/13797);
Semantic Versioning 2.0 (semver.org); Apache Arrow, *Format Versioning and Stability*
(arrow.apache.org/docs/format/Versioning.html); Protocol Buffers proto3 guide (protobuf.dev);
Apache Kafka protocol + magic byte (kafka.apache.org); Fowler, *Parallel Change*
(martinfowler.com/bliki/ParallelChange.html).

**Takeaway:** `major.minor` is the right scheme *and* the thing that bounds adapters/epochs to
majors; `1.0` (not `0.1`) is mandated by the scheme's own definition of `0.x`; read-time adapt is
Avro+upcasting; the index epoch is Postgres's catversion done one better (rebuild, not refuse); the
wire fix is Kafka's magic-byte + ApiVersions. Nothing here is invented — it is assembled.

## §11. Versioning is a BACKEND CONTRACT, not a DuckDB feature (2026-07-02)

**Invariant:** Litmus retains the ability to swap the serving backend (req-6). Therefore
"read every stored version in harmony" is a **required attribute of any backend** — shared across
all of them — even though the *mechanism* is stack-specific. Do not couple versioning to DuckDB.

**The shared, backend-neutral core** (zero serving-backend coupling — this IS the attribute):
- `litmus.data.schema_versions` — the registry (`CURRENT_SCHEMA_VERSION`, `KNOWN_SCHEMA_VERSIONS`,
  `SchemaStore`).
- `litmus.data.schema_dispatch` — the decision (stamp → known adapt / newer **defer** / absent or
  older refuse) + the adapter registry.

Every backend imports and calls these at its own durable→rows boundary. The DuckDB daemon is *one*
implementation; the call sites (`_ingest_one_file`, `scan_sidecars`, …) are the DuckDB-specific
part, not the contract.

**The contract every backend must satisfy** (attribute shared, mechanism per-stack):

| Capability | DuckDB daemon (today) | Snowflake | Iceberg + any engine |
|---|---|---|---|
| Stamp on write | Parquet/Arrow file metadata | file metadata | table schema + payload-version column |
| Dispatch on read (known→adapt / newer→defer / absent→refuse) | `dispatch()` in ingest hooks | `dispatch()` in the Snowpipe/COPY pipeline | `dispatch()` in the reader/ETL |
| Coexist (many versions → one current shape) | `union_by_name` + adapters | `MATCH_BY_COLUMN_NAME` + dbt transform | column-IDs + transform |
| Migrate (opt-in old→current) | `schema_migrate` (parquet/sidecar rewrite) | CTAS / dbt rebuild | rewrite snapshot |
| Version the DERIVED cache | index epoch `_index.e{N}` (#47) | Snowflake table versions / Time Travel | Iceberg snapshots |

**The line to hold:** the **durable-layer** versioning (`schema_versions` + `schema_dispatch` + the
adapter registry) is the **shared contract** every backend calls. The **derived-layer** versioning
(the index epoch, #47) is **legitimately per-stack** — how DuckDB versions *its* cache; a warehouse
versions its own (snapshots / Time Travel). Do NOT force the derived-cache versioning into the
shared contract.

**Code discipline now / at the swap:**
- Now: every backend calls `schema_dispatch` at its boundary; never inline a version check into a
  stack-specific module. `schema_migrate` is the *local* migrate mechanism — another backend brings
  its own (a CTAS) but reuses the *same adapters* from the registry.
- At the second backend (req-6): formalize a `VersionedBackend` Protocol with the five capabilities
  above as methods; the DuckDB daemon becomes one implementation. **Do not build the abstraction
  before the second implementation** (additive-later) — but keep the logic neutral so it drops in,
  which it does today (`schema_versions` / `schema_dispatch` import nothing stack-specific).

## §12. The concrete cloud (and unified local) backend: DuckLake (2026-07-02)

**Litmus stands on the same primitives DuckLake does — DuckDB + Parquet — so DuckLake is a future
*adoption*, not a reinvention to reconcile.** (Integrate, don't reinvent.) Litmus did NOT build a
lakehouse or a table format: it uses DuckDB (an existing engine whose job is querying Parquet) over
Parquet (an existing format) — the standard way to query columnar data. The `_index.duckdb` is
DuckDB caching/indexing Parquet, NOT a hand-built catalog. DuckLake's actual innovation — metadata
as a SQL-catalog *table format* with snapshots + ACID — Litmus does **not** have (it has a
rebuildable derived cache). The fit exists because both stand on the same existing foundation, which
is precisely why DuckLake is clean to adopt: point DuckDB at a DuckLake catalog (Postgres in cloud /
SQLite local) + object store, and it REPLACES the one genuinely-custom piece — the thin daemon /
file-lock coordination glue. That is "integrate, don't reinvent" getting *deeper*, not correcting a
violation of it. DuckLake (DuckDB team; v1.0 Apr 2026) is **Parquet data files + all metadata in a
SQL catalog DB** (Postgres / DuckDB / SQLite), with snapshots, time-travel, arbitrary schema
evolution, and **ACID multi-writer transactions** — the most natural realization of §9–§11 because
it is the *same engine and format* Litmus already integrates.

What it does to this session's open work:
- **Coordination (#41/#42)** → delegated to the transactional catalog DB (ACID multi-writer); the
  singleton-daemon file-lock dance retires.
- **Index epoch (#47)** → schema evolution is a catalog transaction; old/new coexist via snapshots
  (rows in catalog tables) — no per-machine index rebuild/epoch.
- **Defer-and-heal (#43)** → moot: one shared consistent catalog; a "newer file" is a newer
  snapshot it already knows.
- **req-6 swap** → minimal: same DuckDB SQL, same Parquet; point DuckDB at a catalog DB + object
  store instead of a local index + local files. **Unifies local (SQLite/DuckDB catalog) and cloud
  (Postgres catalog + S3)** into one architecture differing only in the catalog host.

Slots into the §11 contract as a `VersionedBackend`: stamp→catalog schema; coexist→schema evolution
+ snapshots; derived-cache versioning→snapshots (no separate epoch). **Keep the Parquet
`schema_version` stamp even under DuckLake** — the catalog tracks schema, but the stamp keeps files
self-describing outside the catalog (§0 portability: cold storage, a customer's own tools).

Caveats to own: catalog round-trip perf (benchmarks show tradeoffs vs file-based formats; fine at
test-data scale); v1.0 maturity (~3 months); the catalog becomes *semi-authoritative* metadata
(snapshot history isn't fully reconstructable from Parquet alone — hence keep the file stamp).

**Signal:** because Litmus is built on the *same existing primitives* DuckLake is (DuckDB + Parquet
— integration, not reinvention), DuckLake is the least-swap cloud path: adopt it, don't rebuild
anything. That makes it the frontrunner over generic ClickStack / Delta+Iceberg for a cloud-hosted
Litmus — precisely because it's the DuckDB-native *integration*, and integrating beats reinventing.

## §13. Normalization / compaction service — the Contract phase (2026-07-02)

The migrate sink (V3, `schema_migrate`) is the per-file primitive: read a below-current file through
its adapter, rewrite it current-stamped, atomic-swap. Scaled to a batch **"normalize the store to
current"** pass, it becomes the operational capability that:

- **Reduces downstream version sprawl.** Litmus owns the adapters, so it is uniquely positioned to
  do the BREAKING-change normalization. Pre-normalizing before a hand-off means Snowflake / Iceberg
  / DuckLake see ONE shape + only additive evolution (which they handle natively) — instead of
  reinventing Litmus's adapters (they can't; they don't have them) or maintaining per-version views.
  Normalize once at the source; the whole downstream simplifies (fewer schemas → simpler ingest,
  stats, planning).
- **Enables adapter retirement.** Once every `vN` file is rewritten to current, nothing reads `vN`
  → delete the `vN → current` adapter. Completes Fowler's Contract phase and BOUNDS the adapter set:
  "support every version forever" ≠ "carry every adapter forever."

Reuses the SAME adapters as the read path (§4, one adapter two sinks) — the read capability turned
toward rewrite. Caveats (per §4): migration is STRUCTURAL not informational (NULL-fills, never
recovers lost info); files in the wild are unreachable (§0), so retirement is a POLICY decision
(like the package-yank), accepting a returning old file would be refused; ALWAYS opt-in (read-time
adapt is the floor). Shape: a batch `normalize_store(store)` + a `litmus data migrate|compact` CLI,
idempotent (current = no-op). Post-0.3.0 (nothing to normalize until a real v2 exists). Under
DuckLake (§12) it is a catalog compaction / rewrite; the primitive is the same. Tracked as #48.
