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

`_index.<fingerprint>.duckdb` — a **single, widened fingerprint** (short 12-char hex).

**One key, not two (resolved 2026-07-04).** The earlier `<schema_epoch>.<fingerprint>` form is
dropped. Everything that decides "can this daemon safely share this index" — the projection DDL,
the adapter registry, the schema whitelist — is a **deterministic function of the daemon's code**
(its litmus version), so it all folds into **one** content-address. A separate `schema_epoch`
component was both *redundant* (an at-rest reshape the projection consumes changes the DDL anyway
→ the fingerprint already forks) and *incomplete* (it never caught an adapter **bugfix**: same
DDL, same epoch, different rows produced from the same parquet). The fix is not a second key — it
is **widening the one fingerprint** to hash the whole ingest→project read-path:

> `fingerprint = sha256(projection DDL  +  registered adapter keys  +  schema whitelist)`

As built, `_projection_fingerprint()` hashes **only** the projection DDL. **P1 widens it** to fold
in the adapter-registry identities + the whitelist, so the name is a true read-path content-address
— not just a projection-shape hash. Then a single key is both *sufficient* (forks on any projection
break) and *complete* (forks on any read-semantics change), and it auto-shares across litmus
versions byte-identical on all three axes — the Nix property (store paths named by an input hash;
versions coexist atomically; upgrades never overwrite).

- **Daemon → exactly one fingerprint**, deterministic (no randomness, no runtime state — it hashes
  the DDL the code emits on a throwaway `:memory:` DB + the adapter/whitelist constants). The
  reverse is many-to-one: one fingerprint ← possibly several behaviorally-identical versions (the
  sharing collapse — putting a raw version string in the name would wrongly *over*-fork it).
- A daemon computes its fingerprint, opens **its own** `_index.<fp>.duckdb` (builds it if absent —
  §4), and **ignores** every other. Rollback is free (the old file was never touched); blue-green
  is structural (old serves while new builds).
- **In the name: 12-char prefix; inside the file: the full 64-char digest.** 8 hex chars would do
  (32 bits, birthday-safe past ~77k files); 12 is used so it never needs a thought.

**The filename is the gate; the in-file `_index_meta` is provenance.** Because a daemon opens only
the file named after its own fingerprint, the shape matches *by construction* — so the MVP's
in-file dual-gate (comparing `schema_version` + fingerprint on open) **collapses**. What stays
inside is a different *kind* of record:

| Inside the file | Job |
|---|---|
| **build-complete marker** (`built_at`, written last, in-txn) | integrity — a crash mid-build leaves a correctly-named but incomplete file; no marker on open → rebuild |
| **provenance** — litmus version + schema_version (human-readable) + the full 64-char fingerprint | display/debug for `litmus data index list` (§7); **not** a routing gate |

`last_seen` (the GC signal) lives **not** in each file but in the shared `_epochs` ledger (§6), so
`gc` and `list` scan access times without opening every DuckDB file.

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
- **Default keep, reap by *dormancy*.** Epochs are **cache, not data** (§5) — so unlike the durable
  stores (whose "keep everything" protects irreplaceable data), keeping every epoch is a
  *lean-eligible* default, not a sacred one. The reap signal is **observed last-access, never a
  "is it dead" oracle** (unknowable) and **never a "forward/upgrade" assumption**: a still-*used*
  older version keeps refreshing its `last_seen`, so LRU self-protects it; only a genuinely
  *dormant* fingerprint ages out. The passive `_epochs` ledger — each daemon stamps
  `(fingerprint, seen_by, last_seen)` on open — is that LRU signal (the ceiling of what's knowable).
- **Never reap the current epoch, or an active/warm one seen in the last N days.**
- **Files are full index replicas, not "small."** Each epoch is the *whole* index over the corpus
  (~0.7 GB per 10k runs, §"disk cost"), and a reaped-then-returning version pays a **full rebuild**.
  The **index build is fast: ~4.5 s** for the whole current corpus (244 files; `:memory:` 3.7 s,
  file-backed + `CHECKPOINT` 4.1 s — fsync adds only ~0.3 s). An earlier "~120 s rebuild" was **not**
  a slow rebuild: it was a **bug in `litmus data index build`'s warmth poll** — it waited for
  `ok == disk_count`, which even one *quarantined* file (incompatible schema) makes unreachable, so
  the poll spun to its 120 s deadline while the daemon sat idle. Fixed (warmth counts *terminal*
  states — `ok` + `quarantined`). So reaping is never a *data* risk (§5) *and* the rebuild it triggers
  is cheap: reap the **dormant**, keep the **active**. `--dry-run` prints the disk reclaimed, framed
  as reclaiming *cache*, never data.
- **Retracted (2026-07-04):** an earlier "auto-reap superseded epochs on upgrade" idea — it punishes
  a still-active older version (Project A pinned to v0.3.0) with a repeated full rebuild every time a
  newer client touches the shared store. Forward motion is not a safe reap trigger; dormancy is.
  "I'm not going back" stays an **explicit** human action (`gc --keep-last 0`), never an auto-default.

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
- **`litmus data index list`** — every epoch file rendered by **human-recognizable identity**, not
  raw hex: short fingerprint, schema version, **BUILT BY** (the version that created it) + **SEEN
  BY** (every version that has opened it — a *set*, since behaviorally-identical versions share one
  file), rows, size, `last_seen`, `*` on the current one, and a total line. Sources the in-file
  provenance (§3) + the `_epochs` ledger. Example:
  ```
    FINGERPRINT   SCHEMA  BUILT BY   SEEN BY         ROWS   SIZE    LAST SEEN
  * e3b0c44298fc  0.1     0.3.1      0.3.1           1.2M   340 MB  2m ago
    a17bfe0091c2  0.1     0.3.0      0.3.0, 0.2.4    1.2M   318 MB  6 days ago
    3 index files · 1.0 GB total · current = e3b0c44298fc (0.3.1)
  ```
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

**P1 — BUILT (`8df7239e`, 2026-07-04):** content-addressed epoch files (§3). `_projection_fingerprint()`
widened to hash the full read-path (DDL + adapter-registry keys + whitelist); filename `_index.<fp>.duckdb`
(single 12-char key, `schema_epoch` dropped); daemon opens only its own; old files persist; in-file
`_index_meta` is now a build-complete marker (`built_at` written last) + provenance (litmus version +
schema_version + full hash), not a shape gate; `_epochs` ledger written on open. Landed with a sweep
race-fix (SATB freeze of the ingest candidate set — a run notified mid-sweep is no longer wrongly pruned)
and the CLI `_index*.duckdb` glob. Crash-loop fixed; coexistence restored.

**P4 + P5 — BUILT (`21e98778`, 2026-07-04):** built out of the suggested order (P5
tooling + P4 retention, ahead of P2/P3) per direct instruction. `_stamp_epochs_ledger` now
accumulates `seen_by` as a sorted, deduplicated set of every `litmus_version` that opened an epoch
(was: overwrite with the latest opener's version), tolerating the pre-P5 single-version ledger
shape on read; a matching `_read_epochs_ledger` reader normalizes both shapes, and
`_remove_epochs_ledger_entries` cleans up reaped/removed entries. `litmus data index list|build|
rm|gc` (§7) land in `src/litmus/cli/data_cmd.py`: `list` renders every epoch by
fingerprint/schema/BUILT BY/SEEN BY/rows/size/last-seen with a `*` current-marker (direct
read-only `duckdb.connect`, falling back to the daemon's Flight SQL surface only for the current
epoch's exclusive lock); `build` blocks until warm (full parquet rescan — copy-seed is P2, not
built) and reports idempotently; `rm` refuses the current epoch without `--force`; `gc` reaps by
the `_epochs` ledger's `last_seen`, honoring `--keep-last`/`--older-than`, always keeping the
current epoch and any epoch of unknowable age (no ledger entry).

**0.3.1 index workstream CLOSED at P1 + P4/P5** (2026-07-04). Everything remaining is a **cache
optimization, not a correctness requirement** (§5 rebuild guarantee) — so it defers with the design
locked and an explicit **trigger**, not another design pass:

- **P2 — cost-ladder copy-seed** (§4): classifier + copy-seed. *Trigger:* a shipped projection change
  creates a real second fingerprint (the actual delta then tells us the rung; rung-2 NULL-lossiness
  makes speculative building unsafe). Also the "avoid the version-change rebuild" lever.
- **P3-b — coexisting per-fingerprint daemons** (§11.1): the real multi-version daemon fix. *Trigger:*
  concurrent multi-version against one shared store, or disk/latency pressure. Build on Gradle/Bazel's
  lifecycle playbook. (P3-a not worth building — dev rules cover its only live symptom.)
  (No cold-rebuild perf item — the "~120 s rebuild" was a `build` warmth-poll **bug**, not slow
  ingest; fixed this session. The real rebuild is ~4.5 s. See §6 + the progress log.)
- **Runtime-dir hygiene:** move socket/pid/port/lock rendezvous out of the data dir into the XDG
  runtime dir. *Trigger:* the `data import` stale-state scrub becomes a real bug. Tracked in tech-debt.

Original suggested order: P1 (safety keystone) → P2 → P5 (makes P1/P2 usable) → P4 → P3. Actual:
P1 → P4/P5 (visible, low-risk, exercises P1) → P2/P3 deferred per the triggers above. Built on
`feat/0.3.1-index-epoch`.

## 11. Decisions (resolved 2026-07-04) + one precondition

1. **RESOLVED — singleton keyed by `(data_dir, fingerprint)`, not by directory (P3).** §2's
   conclusion, sharpened 2026-07-04 and validated against prior art (Gradle daemon, Bazel server +
   `bazelisk`, Nix daemon + store). The daemon binds to its projection **fingerprint** — the
   capability key is the exact read-path content-address, deliberately *stricter* than a
   "compatible-surface" class: it guarantees *same answers*, not merely *bindable columns* (a
   same-surface-but-different-adapter daemon would bind fine and return silently-wrong rows). The
   original "one daemon per **directory**" was a singleton on the wrong key — it held the daemon at 1
   by serving the *wrong model* (Goal 3 violation). The three singleton goals (persistent shared
   index; one writer per index; clients reach a model-matching daemon) are all satisfied by
   daemon↔**index** 1:1, which — once P1 made indices-per-dir > 1 — *requires* per-fingerprint
   daemons. Consequences:
   - **Routing is a local hash.** A client computes its own fingerprint and resolves/launches only
     *its* daemon — no probing, no negotiation.
   - **Launch is inherently per-version.** Spawn is `sys.executable -m litmus.data._runs_duckdb_daemon`
     — a process can only launch a daemon from *its own* venv, so no central authority can spawn
     foreign-version workers. A "global daemon" is therefore at most a **discovery registry**, never a
     **supervisor**; version resolution is a thin `bazelisk`/`gradlew`-style shim, not a service.
   - **Daemons coexist per active fingerprint**, each on its own port (Gradle's model), self-limited
     by idle-death (the process-side GC; disk-side GC is the explicit `litmus data index gc`).
   - **Runtime-state hygiene:** rendezvous files (socket/pid/port/lock) belong in the XDG *runtime*
     dir, not co-mingled in the data dir (per XDG; it's why `data import` must scrub stale state).
   - **Splits into P3-a** (fingerprint-keyed reuse, one daemon/dir — NOT worth building; its only live
     symptom, the dev-loop stale daemon, is covered by dev rules: `reindex` after a projection change)
     **and P3-b** (coexisting per-fingerprint daemons — the real fix). Both **deferred**, design
     locked; trigger = concurrent multi-version against one shared store, or disk/latency pressure.
2. **RESOLVED — single *widened* fingerprint in the name** (`_index.<fp>.duckdb`); `schema_epoch`
   dropped as a name component (§3). The fingerprint is a deterministic function of the daemon's
   code, so DDL + adapters + whitelist fold into one content-address; a second key was redundant
   *and* incomplete (missed adapter bugfixes). P1 widens `_projection_fingerprint()` accordingly.
3. **Precondition (verify in P2) — add-tolerant clients.** The cheap rungs assume clients reference
   columns by name and ignore unknowns (not `SELECT *`→fixed struct, not Pydantic `extra="forbid"`).
   Verify against the Query classes when building P2.

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
- 2026-07-04 — **Design refined + decisions locked** (this session): (a) the filename is a **single
  widened fingerprint** — `_index.<fp>.duckdb`, `<fp>` = 12-char prefix of
  `sha256(projection DDL + adapter-registry keys + whitelist)`; `schema_epoch` dropped as a name
  component (redundant *and* incomplete — missed adapter bugfixes). (b) **The filename is the gate;
  the in-file `_index_meta` becomes a build-complete marker + provenance** (litmus version +
  schema_version + full hash), not a shape gate. (c) `litmus data index list` renders **human
  identity** — schema version, BUILT BY / SEEN BY version sets, rows, size, last-seen, total. (d)
  The `_epochs` **ledger write lands in P1** (open = stamp); P4 adds only the GC policy. §11.1
  (per-version daemons) and §11.2 (single widened fingerprint) resolved; §11.3 remains a P2
  verification. Still no code.
- 2026-07-04 — **P1 BUILT + landed** (`8df7239e`) on `feat/0.3.1-index-epoch`. Content-addressed
  epoch files: `_projection_fingerprint()` widened (DDL + adapter keys + whitelist), filename
  `_index.<fp>.duckdb`, in-file meta is now build-complete marker + provenance, `_epochs` ledger
  written on open, CLI `_index*.duckdb` glob. Shipped with the SATB sweep-freeze race fix (a run
  notified mid-`_ingest_parquet_files` sweep is no longer wrongly cascade-deleted; profiled as a
  runtime non-factor) and a hardened flaky debounce UI test (`f40fa118`). Full pre-commit suite
  green on both commits. P2–P5 remain.
- 2026-07-04 — **P4 + P5 BUILT** (`21e98778`) on `feat/0.3.1-index-epoch` (built ahead of P2/P3 per
  direct instruction). Ledger evolved: `_stamp_epochs_ledger` now accumulates `seen_by` as a sorted set
  (was: overwrite with the latest opener), with a `_read_epochs_ledger` reader tolerating the old
  single-version shape and a `_remove_epochs_ledger_entries` cleanup helper for `rm`/`gc`.
  `litmus data index list|build|rm|gc` land in `src/litmus/cli/data_cmd.py`: `list` sources
  provenance from direct read-only `duckdb.connect` (falling back to the daemon's Flight SQL only
  for the current epoch's exclusive lock) + the ledger, rendered as fingerprint/schema/BUILT
  BY/SEEN BY/rows/size/last-seen with a `*` current-marker and a totals footer; `build` blocks
  until warm via a full parquet rescan (copy-seed is still P2) and reports idempotently (0 new
  files on an already-warm index); `rm` refuses the current epoch without `--force`; `gc` reaps by
  the ledger's `last_seen`, honoring `--keep-last`/`--older-than` (reusing `retention.parse_duration`),
  always keeping the current epoch and any epoch of unknowable age. P2 and P3 remain.
- 2026-07-04 — **`build` warmth-poll bug found + fixed** (during a "why is rebuild slow?" dig): the
  poll gated on `ok == disk_count`, unreachable when any file is quarantined (incompatible schema),
  so it spun to its 120 s deadline on an *idle* daemon. Real rebuild ~4.5 s. Fixed to count terminal
  states (ok+quarantined) + report quarantined; regression test added. (My prior fsync / "116 s
  startup" claims in this doc were wrong guesses, corrected in §6/§10/§11.e.)
- 2026-07-04 — **Design session → decisions locked, workstream CLOSED at P1+P4/P5.** A long convergent
  discussion (validated against Gradle/Bazel/Nix prior art) settling every deferred question the same
  way — *cache optimization, not correctness, so defer with a trigger*:
  (a) the singleton is keyed by `(dir, fingerprint)`, not directory — the original was a singleton on
  the wrong key (§11.1); the capability key is the *exact* fingerprint (result-equivalence), stricter
  than surface-compatibility, on purpose (same-surface-different-adapter would return silently-wrong
  rows). (b) Launch is inherently per-version (`sys.executable -m …` from the client's own venv), so a
  "global daemon" is a discovery *registry*, not a *supervisor* (the `bazelisk` shim pattern).
  (c) Retention = LRU-by-**dormancy**; epochs are **cache, not data**; never reap an active/warm epoch;
  "I'm not going back" is an explicit human action, never an auto-forward default (§6). (d) **Retracted
  two mid-session missteps:** "auto-reap superseded epochs on upgrade" (punishes a still-active old
  version with repeated rebuilds) and "global daemon as supervisor" (can't launch foreign versions).
  (e) **"Indexes are slow to build" — investigated to root cause, and it was a BUG in our own
  tooling, not perf.** Chain of my errors, each refuted by the next measurement: (1) a toy `SELECT *`
  `:memory:` read (0.46 s) mistaken for "the index build"; (2) an *unproven* "~250× fsync-dominated"
  claim — refuted by file-vs-`:memory:` (4.1 s vs 3.7 s, fsync ≈ 0.3 s); (3) an *unproven* "~116 s
  non-ingest startup overhead" — refuted by inspecting `_ingested`: **242 ok + 2 quarantined**. Root
  cause: `litmus data index build`'s warmth poll waited for `ok == disk_count`, which quarantined
  files (never `ok`) make unreachable → it spun to its 120 s deadline while the daemon sat **idle**.
  The real rebuild is **~4.5 s**. Fixed (warmth counts terminal states `ok`+`quarantined`; reports
  quarantined) + regression test. Lesson: I asserted a cause three times before measuring; each guess
  was wrong. "Profile before you assert" — the profile was "daemon idle, CLI sleeping in a bad loop."
  Everything past P1+P4/P5 (P2 copy-seed, P3-b coexisting daemons, cold-rebuild perf tune, XDG
  runtime-dir hygiene) is deferred **with triggers** (§10), not re-litigated.

---

## Appendix — original MVP contract (superseded by §1–§4 above, retained for the build record)

The MVP was: a projection fingerprint (recording-proxy SHA256 of `_ensure_schema`+`_create_views`
DDL, whitespace-normalized, shape-DDL only), stamped `(schema_version, projection_fingerprint)`
into an `_index_meta` table; `_open_index` reads the stamp and, on mismatch (or missing/corrupt),
funnels through `_reset_index` + `_stamp_index_meta` → `is_fresh=True` → cold-start re-ingest from
parquet. Both self-heal paths (corrupt file; stale shape) share that discard→rebuild→re-stamp path;
a matching index opens with `is_fresh=False`. Tests in `test_runs_index_selfheal.py`. This remains
the in-place fallback that P1–P2 generalize into content-addressed files + copy-seed birth.
