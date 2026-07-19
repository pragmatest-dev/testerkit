# Versioning & index resiliency — deferred backlog (post-0.3.1)

**Status:** shaping doc / roadmap (2026-07-05). Consolidates every item deferred out of the
0.3.1 runs index-epoch + schema-versioning work into one trigger-driven backlog. The **locked
design detail** for the index-internal items lives in
[`derived-index-versioning.md`](derived-index-versioning.md); this doc is the single "what's
parked, and what wakes it up" view. Do not duplicate the design here — point to it.

---

## The guiding principle (why everything below is *deferred*, not *dropped*)

0.3.1 hardened the axis that's irreversible and cheap to insure: **data survives upgrades** (at-rest
adapters + content-addressed index epochs + rebuild-from-parquet). Everything left is one of:

- a **cache optimization** (the derived index is a pure, rebuildable cache — §5 of the contract), or
- a **scale-tail** concern (large/shared/multi-version stores), or
- a **1.0 contract** concern (freezing consumer-facing APIs).

The measured reality that justifies deferral: a dormant epoch is an **inert file** (dead daemon — no
RAM/CPU), costing only disk (~74 KB/run; tens of MB for a typical store), and a rebuild is **~1 ms/row**
(seconds at typical scale). So keeping *or* rebuilding is cheap for the 90%; the machinery below only
earns its keep in the tail. **Decision rule:** build an item when its *trigger* fires — a real workload
symptom — not on a calendar, and not speculatively (speculative building is how we'd re-introduce the
rung-2 lossiness / over-engineering we deliberately avoided).

Each item lists a **Trigger** (the symptom that makes it worth building) and a **Design** pointer.

---

## A. P2 — cost-ladder copy-seed birth

- **What:** born a new epoch by *copy-seeding* from the nearest existing epoch + a forward-transform
  (cost-ladder rungs: view-add / column-add / reshape / rescan), instead of a full parquet rescan.
- **Why deferred:** no live *second* fingerprint exists yet, and the contract's rung-2 (`cp` +
  `ALTER ADD COLUMN`) is **silently NULL-lossy** for parquet-derived columns — only rung-1 (view-only
  additions) is provably correct via copy-seed. Building it speculatively re-introduces that trap.
- **Trigger:** a shipped projection change creates a real second fingerprint — the *actual* delta then
  tells us which rung is needed (and whether rung-2 must re-derive from parquet vs. NULL-fill).
- **Design:** `derived-index-versioning.md` §4 (cost-ladder), §9 (worked example), §11.3 (add-tolerant
  clients precondition — verify against the Query classes when building). Prior art: Lucene
  `IndexUpgrader`, pg_upgrade copy-mode, dbt `clone`.
- **Cost / risk:** moderate; correctness-sensitive (transforms). *The* value: turns a cold rebuild into
  a sub-second copy for the common additive change.

## B. P3-b — coexisting per-fingerprint daemons (+ the global index agent)

- **What:** move the daemon identity key from `(data_dir)` to **`(data_dir, fingerprint)`**, so
  incompatible projection versions each run their *own* daemon on their *own* port and coexist without
  thrash. Optionally, a per-user **standing "index agent"** (a discovery *registry* — the
  ssh-agent/gpg-agent/nix-daemon/docker pattern), the symmetric partner to the global data store.
- **Why deferred:** concurrent multi-version against one shared store isn't live (single dev, single
  venv). The current version-ratchet + [dev rules](#dev-rules) cover today's reality. And **launch is
  inherently per-version** (`sys.executable -m …` from the client's own venv), so a "global daemon" can
  only ever be a *registry*, never a *supervisor* that spawns foreign versions — which caps how much a
  standing agent buys until true coexistence is needed.
- **Trigger:** two testerkit versions querying one shared `data_dir` at overlapping times (version-skewed
  projects sharing the global store), or daemon RAM/thrash pressure.
- **Design:** `derived-index-versioning.md` §2, §11.1 (singleton-per-fingerprint; result-equivalence
  capability key; launch-is-per-version; registry-not-supervisor). Adopt Gradle's daemon-lifecycle
  playbook (compatibility-keyed reuse, fast redundant-daemon expiry, memory-aware self-stop,
  `--status` visibility). Composes with the req-6 serving-tier swap (`acquire → opaque location` is
  already the seam).
- **Shared with F:** the fix lands in the **shared `DaemonManager.acquire`** (all four stores subclass
  it) — reuse-key changes from `testerkit_version` to fingerprint via a subclass hook. So B and **F (#64
  cross-store parity)** are the *same seam*: do the base fix once and every store gets per-version
  binding. See §F.
- **Cost / risk:** largest item; revises the singleton lifecycle. No cheaper option exists (one daemon
  per active incompatible fingerprint is the floor).

## C. Retention policy refinements

- **What:** (1) a **size-aware** prune default (reap by *size × age*, not age alone, so a large store
  gets reclaimed while a small one is left alone); (2) a **post-upgrade version-change nudge** — suggest
  `testerkit setup` + `testerkit data index prune` once, on a detected version bump.
- **Why deferred:** retention barely matters at small scale (dormant epochs are inert, tens of MB;
  rebuild ~seconds). The **size-gated `old_epoch_hint`** (fires ≥ 1 GiB, shipped) already covers the
  visible case. A startup nudge needs a *new* cross-cutting mechanism (global last-seen-version state +
  an every-command hook) — over-reach without a real need.
- **Trigger:** large stores hitting real disk pressure, or user demand for post-upgrade nudging.
- **Design:** `derived-index-versioning.md` §6 (retention = LRU-by-dormancy, cache-not-data, "not going
  back" is an explicit human action). Current state: `testerkit data index prune` + the size-gated setup
  hint.

## D. XDG runtime-dir hygiene

- **What:** move the daemon's **runtime rendezvous** (socket / pid / port / lock / state files) out of
  the data dir into `platformdirs.user_runtime_dir` (`$XDG_RUNTIME_DIR`), keyed by
  `(data-dir, fingerprint)`. The single seam is `data_cmd._resolve_runs_dir`.
- **Why deferred:** works today (co-mingled in the data dir). The smell is that `testerkit data import`
  must *scrub stale pid/port state* copied from another machine — runtime artifacts riding along with
  durable data they don't belong with.
- **Trigger:** the import stale-state scrub becomes a real bug, **or** P3-b's per-fingerprint state-file
  keying is built (the natural moment to relocate them). Currently tracked under tech-debt (#58).
- **Design:** the XDG Base Directory Specification — `RUNTIME_DIR` is *for* sockets/pids (ephemeral,
  per-login, often tmpfs); `DATA_HOME` is for durable data.

## E. API / consumer-contract versioning

- **What:** version the **consumer-facing** contracts — Query API return models, MCP tool responses,
  HTTP/JSON endpoints, `testerkit show -f json/csv` — so an upgrade doesn't silently break user code,
  dashboards, or integrations. A **distinct axis** from data versioning: data-survives vs.
  consumer-code-survives.
- **Why deferred:** deliberately a **1.0** concern. Pre-1.0 the API is expected to churn (SemVer 0.x);
  formally versioning a contract you're still designing is an anti-pattern, and the harm (a broken
  query/panel) is *recoverable*, unlike data loss. Pre-1.0 posture: **unversioned-but-disciplined** —
  minimize breaking shape changes, document them in release notes.
- **Trigger:** approaching 1.0 (commit to a stable contract). **Near-term, independent:** fix the
  **Grafana server** (#58), which bypasses the Query API and *hand-reimplements the projection over raw
  parquet* — a live, drift-prone second copy of the contract; and be deliberate about breaking the
  sticky HTTP / CLI-export shapes.

## F. Cross-store parity — events / channels / files (#64)

- **What:** bring the events, channels, and files derived indexes to **runs parity** — but *not* by
  cloning runs three times. **Extract the shared spine; keep per-store what's per-store.**
- **Verified structure (2026-07-05):** all four managers (`RunsDuckDBManager`, `DuckDBDaemonManager`
  [events], `FilesCatalogManager`, `FlightDaemonManager` [channels]) subclass the **same
  `DaemonManager`**; only runs is versioned today. **Events is runs' near-twin** (DuckDB daemon,
  `_index.duckdb`, `_ingested` ledger, incremental ingest); **channels/files are *catalog* daemons**
  (metadata over arrow segments / blobs, live upserts) — simpler, more stable projections.
- **Three layers of applicability:**
  1. **Already universal (verify, don't build):** the rebuild-from-at-rest guarantee holds for every
     store (each `_index.duckdb` is a cache over durable at-rest data), so cache-not-data + LRU
     retention (§C, §6) apply to all four unchanged.
  2. **Shared spine — extract once, all four benefit:**
     - **Per-fingerprint daemon binding belongs in `DaemonManager.acquire` itself** (it reuses by
       `testerkit_version` today). Change the reuse key to the **fingerprint via a subclass hook**, done
       once in the base → every store gets correct per-version binding. Runs overrides the hook with
       `_projection_fingerprint`; the others override as they gain a fingerprint. **This is the same
       seam as item B (P3-b)** — B and F share it; do the base fix once and both land.
     - The content-addressed epoch mechanics (`_index.<fp>.duckdb` naming, build-complete marker +
       provenance in `_index_meta`, the `_epochs` ledger, retention) — currently inline in
       `_runs_duckdb_daemon.py` — hoist into a **shared helper module** each daemon calls with *its
       own* fingerprint. So #64 is mostly *extraction*, not re-implementation.
  3. **Per-store — compute-your-own + verify:** each daemon computes its own fingerprint from its
     read-path. **Verify (not assume):** does events' ingest sweep have the same SATB cascade-delete
     race as runs (likely — same `_ingested`/incremental shape → apply the freeze)? Channels/files use
     live catalog upserts → different pattern → check separately. And how shape-coupled is each store's
     query layer (runs is heavily coupled via the measurement projection; the others are catalog-ish →
     lower coexistence pressure).
- **The judgment (over-engineering lens):** the other stores churn *less* (simpler, stabler
  projections). Apply the **cheap spine** (per-fingerprint binding + content-addressed epochs) broadly
  — it's the coexistence / no-crash-loop insurance, and pre-1.0 all projections still change. **Skip
  the expensive parts** (copy-seed §A, heavy per-store tooling) unless a specific store's rebuild proves
  costly (smaller indexes → avoiding a rebuild matters less).
- **Why separate:** its own workstream (**task #64**), gated on runs P1 — now validated. The one item
  in this backlog **ready now** (not waiting on an external trigger).
- **Sequence:** base `DaemonManager` fingerprint hook + shared epoch helper → **events** first (closest
  twin, verify+fix the sweep race) → the two catalogs. Reuse, don't re-implement.

---

## Suggested sequencing (trigger-driven, not calendar)

1. **#64 (cross-store parity)** — ready now; the runs pattern is proven and reusable. The only item not
   waiting on an external trigger.
2. **E's near-term slice (Grafana projection-copy, #58)** — a live drift landmine, independent of the
   1.0 API-versioning work.
3. **Everything else waits for its trigger:** P2 on the first real 2nd fingerprint; P3-b + D on shared
   multi-version use or disk pressure; C on large-store disk pressure; E (full) at 1.0.

Do **not** batch these onto a release calendar. Each is cheap to defer (rebuild-from-parquet guarantee)
and expensive to build speculatively; wake each one only when its symptom is real.

<a name="dev-rules"></a>
## Meanwhile — dev rules that stand in for the deferred code

Until P2/P3 land, the dev-loop friction they'd smooth is handled by procedure, not code:

- **Changed the projection** (schema DDL, `_create_views`, `_*_PERSISTED_COLUMNS`, an adapter, or the
  whitelist)? The fingerprint moved → run `testerkit data reindex` (or `testerkit data index build --rebuild`)
  before the next interactive query, so a stale daemon doesn't serve the old shape.
- **Changed the at-rest parquet shape** (extraction/materialization)? Wipe `data/` — reindex alone
  won't help; old parquet keeps the old shape.
- **Stuck daemon / "column not found" that won't clear?** `testerkit data reindex`, and kill any stray
  `testerkit mcp serve` before daemon-spawning test runs.
- Tests need nothing — conftest kills daemons + resets `data/` at session start.
