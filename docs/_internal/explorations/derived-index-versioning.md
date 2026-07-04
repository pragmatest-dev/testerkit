# Derived-index versioning — per-version isolation + cost-ladder + lifecycle tooling (#47/#53)

**Status:** reconciled design contract (2026-07-03). Supersedes the MVP-only framing
below; folds in `schema-versioning-migration.md` §8/§9 and `pre-1.0-epoch-strategy.md`.
This is the contract for the **derived-index (DuckDB) versioning** workstream of #53.
The **durable-layer** versioning (stamp / dispatch / adapters / golden corpus) is a
separate, already-shipped concern — see `schema-versioning-migration.md`.

> **One-line thesis:** the derived index is a pure, always-rebuildable cache over durable
> parquet, so we version it by **full per-version isolation** (each projection version gets
> its own index file, built cheaply by copy-seed), never by trying to share one file across
> incompatible versions.

---

## 0. What shipped (the MVP) and why it isn't enough

Shipped on `main` (`e0add12d`): a **projection fingerprint** (`_projection_fingerprint()` —
a recording-proxy SHA256 of the exact `_ensure_schema`+`_create_views` DDL, whitespace-
normalized) stamped into `_index_meta` inside a **single** `_index.duckdb`; on boot,
`_open_index` compares the stored `(schema_version, fingerprint)` to the code's and, on
mismatch, **discards and rebuilds in place**.

That gives crash-safety on a *single* machine/version. It does **not** give what §9 and the
pre-1.0 strategy require:

- **No side-by-side coexistence.** One file, discarded on mismatch → an old and a new version
  cannot both use the data dir; every version flip re-reads all parquet (rebuild thrash);
  there's an empty window mid-rebuild.
- **The fingerprint is in the *stamp*, not the *filename*** — so it can't name coexisting files.
- **Whole-file rebuild is the only rung** — no ALTER-in-place, no copy-seed.

The reframe below fixes all three.

## 1. The reframe — two orthogonal version coordinates

The MVP collapsed two different jobs into one crude mechanism. They are separate axes:

| Axis | Job | Mechanism |
|---|---|---|
| **A — the index *filename*** | coexistence: incompatible versions never clobber each other | **content-addressed** name; a daemon opens only *its own* file, others persist untouched |
| **B — how a file is *built*** | cheap birth instead of full rescan | **cost-ladder**: copy-seed + ALTER / transform / rescan |

Axis A is *isolation*; Axis B is *cost*. The MVP had neither (single file, whole rebuild).

## 2. The load-bearing decision — the derived layer is **fully per-version-isolated**

**Revises `schema-versioning-migration.md` §8 (singleton-daemon ratchet + piggyback) and §9
("same-major daemons share one index file").** Those assumed readers *tolerate* a newer
projection. Ours do not:

**Our query layer is SQL-coupled to the projection shape.** `RunsQuery`/`MeasurementsQuery`
don't do tolerant `SELECT *` reads — they name views, columns, and joins and ship that SQL to
the daemon over Flight. So "additive to *storage*" (add a nullable column, old readers ignore
it — the Iceberg/dbt model) does **not** transfer: a projection change that renames, drops, or
restructures anything an older client's SQL references **breaks that client**, even with zero
new stored information. Proven tonight — the snowflake, the EAV→inputs/outputs split,
`measurement_facts`, the `index` column all changed the shape queries depend on.

Therefore:

- **Durable layer (parquet)** — the *one* shared, versioned artifact. `schema_dispatch` +
  adapters read any version forward (already built). This is the only cross-version contract.
- **Derived layer (daemon + index + the SQL that queries it)** — **per-version, never shared.**
  Each active version binds its **own** daemon, **own** index file, and its **own** SQL, all
  moving together by construction → there is *never* a cross-version SQL mismatch. Old versions'
  daemons die on the idle-timer; their index files persist (copy-seeded, rebuildable) until that
  version runs again or GC reaps them.

This deletes a whole class of bugs (the crash-loop, the SQL-column-not-found skew) instead of
hardening against them. Cost accepted: potentially one daemon per *active* version (bounded —
§8's 300s idle-kill reaps abandoned ones; most machines run one version) and more small index
files (cheap — copy-seed birth + rebuildable). Multiple package versions with an *identical*
projection share one file (content-address collides), so the file count tracks distinct
*projection shapes*, not releases.

## 3. Axis A — content-addressed index filenames

`_index.<schema_epoch>.<projection_fingerprint>.duckdb`

- `schema_epoch` = leftmost-significant semver component of the runs `schema_version`
  (pre-1.0: the minor — `0.1`,`0.2`; post-1.0: the major). Guards the at-rest axis.
- `projection_fingerprint` = the shipped DDL fingerprint (short hex). Guards the projection
  axis — the snowflake was a projection-only break with **no** `schema_version` bump, so the
  filename must key on it too, or that class of change silently crash-loops (the MVP's bug).

A daemon computes its `(schema_epoch, fingerprint)`, opens **its own** file (builds it if
absent — §4), and **ignores** every other. This is the Nix property (store paths named by an
input hash; versions coexist atomically; upgrades never overwrite). Rollback is free (the old
file was never touched); blue-green is structural (old serves while new builds).

**Fork granularity, settled:** because §2 isolates fully *and* §4 makes birth cheap, we do
**not** chase "share one file within a minor" (§9's piggyback, shown unsafe by SQL-coupling).
Every distinct projection fingerprint gets its own file. The additive-vs-breaking distinction
does **not** decide *whether* to fork (always fork per fingerprint) — it only decides *how
expensively the new file is born* (§4). This is what dissolves the "won't that make too many
files?" worry: additive births are near-free.

## 4. Axis B — the cost-ladder governs **birth speed**, not sharing

When version V's index file is absent, seed it cheapest-first from the nearest existing epoch
file (Lucene `IndexUpgrader` / pg_upgrade **copy-mode** / dbt `clone`):

| Rung | Diff of V's projection vs the nearest existing file | Action | Cost |
|---|---|---|---|
| 0 | identical fingerprint | it *is* that file — open it | none |
| 1 | adds only new views | `cp` + `CREATE OR REPLACE VIEW` | ~instant |
| 2 | adds only new columns | `cp` + `ALTER TABLE ADD COLUMN` | metadata-only |
| 3 | a column/table renamed·dropped·retyped·restructured, transform expressible | `cp` + forward-transform (`ALTER … DROP/RENAME`, reshape) | O(index size), no parquet |
| 4 | no expressible transform | full parquet rescan through `schema_dispatch` | O(all files) |

**Classifier** (which rung): diff the code's expected surface (`_*_PERSISTED_COLUMNS` + view
defs) against the nearest file's live surface (`DESCRIBE`). Additions-only → rung 1–2;
existing-surface delta → rung 3; unrepresentable → rung 4.

**Copy, not hardlink** (pg_upgrade copy-mode, *not* link-mode): the new file is independent of
its parent, so reaping the parent never harms it, and rollback keeps the parent intact. The
copy is *one-way* — it diverges forward; the old file is never mutated.

**#31 as the worked example:** dropping `record_type` at rest (schema 0.2) is a rung-3 birth —
`cp` the 0.1 epoch's index + `ALTER … DROP COLUMN record_type`, sub-second, no rescan.

## 5. The foundation — rebuild-from-at-rest is guaranteed

Everything above is affordable *because* the index is a pure cache over durable parquet, and
that is guaranteed by two invariants we already hold and test:

1. **Ingest reads only at-rest** — nothing lives solely in the derived layer (the drift tests
   guard projection *completeness*; rebuildability is the trivial reverse).
2. **Adapters-forever (BACKWARD_TRANSITIVE)** — newest code reads every version, forward
   (`schema_dispatch` + registry, pinned by the golden `0.1` corpus + synthetic-adapter test).

Consequence: **deleting any index file is always correctness-safe** — a reaped epoch rebuilds
from parquet if its version returns (worst case: CPU, never data). The honest floor: you can
always rebuild *the best index the at-rest data supports* — a lossy old file NULL-fills what it
never captured (migration normalizes shape, not information). **Mental model: at-rest is the
pet, the derived layer is cattle.**

## 6. Retention & removal — safe because reversible

The *only* place "we can't know how many versions are at play" bites is GC; correctness never
needs to know the version set (content-addressing is self-organizing — each daemon resolves its
own file). And GC is defused by §5: removal is never a data risk.

Policy:
- **Default keep everything** — epochs are rare + files small; the set is a handful.
- **Reap by *observed last-access*, never a "is it dead" oracle** (unknowable). A passive
  `_epochs` ledger in the data dir — each daemon stamps `(fingerprint, last_seen)` on open —
  gives an LRU signal from the past (the ceiling of what's knowable).
- **Never reap the current epoch, or one seen in the last N days** (an actively-flapped version).
- A reaped-then-returning version just rebuilds. `--dry-run` prints the bet, never a data-loss
  warning, because nothing here is precious.

Prior art: Nix `nix-collect-garbage` (gc-roots + reachability), DuckLake `expire_snapshots`,
OS cache reapers.

## 7. Lifecycle tooling — `litmus data index …`

Operational half of the epoch design; sibling to the durable-side `litmus data migrate|compact`
(#48). One `litmus data` namespace, split by layer.

- **`litmus data index build [--rebuild] [--background]`** — eagerly construct the *current*
  epoch (copy-seed per §4, else rescan). Idempotent (catches up the delta if already warm).
  **Reports the path taken** ("seeded from `a1b2c3` + dropped record_type in 0.4s" vs "rebuilt
  from 12,400 parquet in 44s" — no silent stall). Not redundant with lazy daemon build: it
  **blocks until warm** (a script *knows* it's ready), is **scriptable**, lets you **choose**
  copy-seed vs rescan, and **reports**.
- **`litmus data index list`** — every epoch file: short fingerprint, schema-epoch, rows, size,
  `last_seen`, `*` on the current one.
- **`litmus data index rm <fingerprint>`** — drop one; refuses the current one without `--force`.
- **`litmus data index gc [--keep-last N] [--older-than 30d] [--dry-run]`** — reap by the ledger.

**The upgrade-warm workflow** (pg_upgrade / blue-green, done deliberately):
```
pip install --upgrade litmus-test     # new version → new projection fingerprint
litmus data index build               # copy-seed the new epoch from the old, forward-transform — fast
pytest                                # runs warm; no first-query rebuild stall
# …later, once you won't roll back:
litmus data index gc --older-than 30d # reclaim the old epoch's disk
```
Copy-seed is what makes "build the green epoch ahead of cutover" cheap enough to be a routine
pre-test step.

**Setup vs data command:** `litmus data index build` is the primitive; `litmus setup` may *call*
it as an optional "warm the index now?" step — never duplicate the logic.

## 8. Prior art (web-verified 2026-07-03)

| Litmus mechanism | Prior art | Verdict |
|---|---|---|
| Content-addressed index files, versions coexist | **Nix** store paths (`<hash>-name`) | canonical |
| Copy-seed birth (copy old file + transform) | **Lucene `IndexUpgrader`** (rewrite segments, no reindex); **pg_upgrade copy-mode**; **dbt `clone`** | textbook |
| Copy **not** hardlink (rollback/GC-safe) | pg_upgrade copy vs link mode | deliberate |
| Cost-ladder rungs (view / ALTER / partial / full) | **Iceberg** metadata-only add-column; **dbt state:modified** per-model; **IVM** delta-apply | assembled |
| Fingerprint = whitespace-normalized DDL hash | **dbt state:modified** (semantic SQL hash) | same idea |
| Old serves while new builds | **blue-green** projection rebuild (Marten/Critter) | matches |
| Rebuild-from-source over refuse/reindex-in-place | vs Postgres catversion (refuse) / Lucene N-1 (reindex) | *better* — we rebuild |
| GC by last-access, reversible | Nix `nix-collect-garbage`; DuckLake `expire_snapshots` | matches |

**Why not just adopt DuckLake?** DuckLake versions *data* (a table format for a source of
truth: snapshots, schema-evolution, time-travel). Tonight's problem is versioning a
*code-defined projection whose SQL is coupled to its shape* — an application-code problem no
table format solves. Proof: even on DuckLake, a v1.0 client can't get *current* data in the
*v1.0 projection shape* unless v1.0's projection code runs (time-travel gives old-shape only at
old-*data*). DuckLake remains the right **durable-layer** future backend (`§12`), and this
per-version isolation sits on top of it unchanged (`§11`: derived-cache versioning is per-stack).

## 9. Worked example — what forks, from the real roadmap

Classifying #56/#57 against §3/§4 (the design predicts what the roadmap already flagged by hand):

- **Cheap, no new epoch (additive):** #4 fixture identity columns (like tonight's identity
  exposure), #26 StepsQuery in/out filter surface, #9 in-body vector redo API.
- **The one guaranteed epoch:** **#31** drop `record_type` at rest → schema `0.1→0.2`, needs the
  first real adapter, restructures the measurements projection. Both axes agree. Roadmap already
  tagged it "⚠ #31 → schema 0.2." Born via rung-3 copy-seed (`ALTER DROP COLUMN`), no rescan.
- **Depends:** #21 fixture-connection reverse-XOR/merge — epoch iff it reshapes existing at-rest
  fields; no-op iff a read-time derived merge.

**The record_type projection drop (shipped 2026-07-03) as the pre-vs-post-release illustration:**
the *same* 8-site edit is **free pre-release** (single version → daemon rebuilds the index on the
changed fingerprint) but **post-release would be a breaking, epoch-forking projection change**
(removes a column our own query SQL named → old clients get `column record_type not found` against
a new daemon's view). Without per-version isolation it's a poison pill in the shared data dir;
with it, old daemons keep their epoch, new ones fork (copy-seed). This is exactly *why* we do
breaking projection cleanups now, at 0.x, before release.

## 10. Built vs remaining (state-checked 2026-07-03)

**Built + green:** durable-layer stamp (`schema_versions`, all stores `0.1`), whitelist-dispatch
reader (known→adapt / newer→defer / absent→quarantine) + `register_adapter` seam, golden `0.1`
corpus, synthetic-adapter re-index+migrate tests, mixed-version coexistence test; the projection
**fingerprint** + in-place self-heal (the MVP).

**Remaining (this contract):**
- **P1 — content-addressed epoch files** (§3): filename `_index.<schema_epoch>.<fp>.duckdb`;
  daemon opens only its own; old files persist. *Fixes the crash-loop; restores coexistence.*
- **P2 — cost-ladder birth** (§4): classifier + copy-seed (rung 1–4). *Makes P1 cheap.*
- **P3 — per-version daemon binding** (§2): a client binds a daemon of its own projection version;
  never cross a version boundary. *Revises §8 singleton-ratchet.*
- **P4 — retention** (§6): `_epochs` last-access ledger + GC policy.
- **P5 — tooling** (§7): `litmus data index build|list|rm|gc` + the upgrade-warm workflow.

Suggested order: P1 (safety keystone) → P2 → P5 (makes P1/P2 usable) → P4 → P3 (largest, revises
lifecycle). Build on a branch (`feat/0.3.1-index-epoch`) — multi-commit, half-functional
intermediate states.

## 11. Open decisions to confirm before building

1. **Per-version *daemons* (P3) vs. one daemon serving multiple epoch files.** §2 concluded
   per-version daemons (only old code can keep an old epoch *current*). This is the biggest
   revision to §8 — confirm before P3.
2. **Filename: include `schema_epoch` explicitly, or fingerprint-only?** Recommended:
   `<schema_epoch>.<fp>` (belt-and-suspenders: a schema bump that doesn't change the DDL text
   still forks). Cheap to include.
3. **Add-tolerant-client precondition** — the cheap rungs assume clients reference columns by
   name and ignore unknowns (not `SELECT *`→fixed struct, not Pydantic `extra="forbid"`).
   Confirm/ensure when building P2.

---

## Progress log

- 2026-07-03 — **MVP built + shipped** (`e0add12d`): fingerprint + rebuild-on-mismatch, single
  `_index.duckdb`, stamp in `_index_meta`. (Details preserved below.)
- 2026-07-03 — **Reconciled contract written** (this rewrite) after a long design session.
  Findings that drove it: (a) the derived layer must be **fully per-version-isolated** because the
  query layer is **SQL-coupled** to the projection — §8/§9's shared-index piggyback is unsafe;
  (b) the fingerprint belongs in the **filename** (content-addressed, Nix-style), not just the
  stamp; (c) the **cost-ladder** is not superseded — it governs **birth speed** (copy-seed, rungs
  1–4), and epochs can start from a **copy** of the old (pg_upgrade copy-mode); (d) removal is
  always safe (rebuild-from-at-rest guarantee) → GC by **last-access ledger**; (e) added the
  `litmus data index` **lifecycle tooling** (build/warm + list + rm/gc) + the upgrade-warm
  workflow. Prior art web-verified (Nix, Lucene IndexUpgrader, pg_upgrade, dbt clone/state:modified,
  blue-green, IVM, Iceberg; DuckLake is the durable-layer future, not this). Ships as #53
  workstream P1–P5 on a branch. No P1–P5 code yet.

---

## Appendix — original MVP contract (superseded by §1–§4 above, retained for the build record)

The MVP was: a projection fingerprint (recording-proxy SHA256 of `_ensure_schema`+`_create_views`
DDL, whitespace-normalized, shape-DDL only), stamped `(schema_version, projection_fingerprint)`
into an `_index_meta` table; `_open_index` reads the stamp and, on mismatch (or missing/corrupt),
funnels through `_reset_index` + `_stamp_index_meta` → `is_fresh=True` → cold-start re-ingest from
parquet. Both self-heal paths (corrupt file; stale shape) share that discard→rebuild→re-stamp path;
a matching index opens with `is_fresh=False`. Tests in `test_runs_index_selfheal.py`. This remains
the in-place fallback that P1–P2 generalize into content-addressed files + copy-seed birth.
