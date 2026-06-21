# Read-surface unification — one consistent way to query each resource

**Status:** PROPOSAL, 2026-06-21. Needs a design decision before any build. Supersedes the
narrow framing of task #3 ("add Channel/File/Event `*Query` classes") with the broader
question it's really pointing at. Nothing here is built.

---

## 1. The problem, precisely

Litmus has seven read-able resources. The way you read each one is **inconsistent on three
tiers**:

| Resource | Analytical `*Query` | `*Store` | In public `litmus.queries`? | How you read it today |
|---|---|---|---|---|
| Run | `RunsQuery` | `RunStore` | `RunsQuery` | **two paths** (daemon *and* file) |
| Step | `StepsQuery` | — (in run parquet) | `StepsQuery` | `StepsQuery` (daemon) |
| Measurement | `MeasurementsQuery` | — (in run parquet) | `MeasurementsQuery` | `MeasurementsQuery` (daemon) |
| Event | — | `EventStore` | **`EventStore`** | a *Store* re-exported as a "query peer" |
| File | — | `FileStore` | ❌ | reach into `FileStore` directly |
| Channel | — | `ChannelStore` | ❌ | reach into `ChannelStore` directly |

Two distinct smells fall out:

1. **Channel / File / Event have no `*Query`.** Events get re-exported from `litmus.queries`
   as a bare `*Store` (the "odd one out" the module docstring admits); Channels and Files
   aren't on the public query surface at all — callers reach into the stores.
2. **Run has *two* read surfaces that overlap and even disagree on return type.**
   `RunStore.get_run → RunSummary` (reads parquet files directly); `RunsQuery.get → RunRow`
   (reads the DuckDB daemon over Flight). Same data, two code paths, two shapes.

## 2. The key distinction (why this isn't just "add three classes")

The existing `*Query` classes are **not thin wrappers** — they're a genuine **DuckDB-daemon
analytical layer**: aggregation and cross-row queries (`failure_pareto`, `count_by_outcome`,
`usage_stats`, `cpk`, `parametric`, yield) over the parquet that the stores write. That layer
earns its existence for Runs/Steps/Measurements.

Channel / File / Event are **different in kind**:
- `ChannelStore` already has `query()` / `query_registry()` over its **own** Flight daemon — a
  `ChannelQuery` would mostly re-expose those.
- `FileStore` is blob storage (`read`/`read_range`/`size`/`read_attributes`) — no analytical
  layer exists or is wanted; a `FileQuery` is a pure facade.
- `EventStore` already *is* the read API (`events()`/`sessions()`) — an `EventQuery` adds
  almost nothing.

So the honest finding: **Channel/File/Event don't need an analytical query layer.** What's
inconsistent is not "they lack aggregation" — it's "there's no uniform *entry point* and
*shape* for reading a resource." The fix is about the **public surface and naming**, not new
analytical machinery.

## 3. What "consistent" should mean

The resource-centric principle (already followed by the MCP layer: `litmus_runs` /
`litmus_steps` / `litmus_events` / `litmus_channels` / `litmus_files`) says: **one obvious
read entry point per resource, reachable from one place.** Today `litmus.queries` is that
place for Runs/Steps/Measurements (+ a re-exported EventStore), but Channels/Files are
missing and Events are shaped differently.

Two things are worth separating:
- **(a) Analytical reads** (aggregate, cross-row) — a real capability that only
  Runs/Steps/Measurements have and need. Keep as `*Query`.
- **(b) Resource access** (list/get/read one resource's data) — every resource needs this; the
  stores already provide it.

The inconsistency is that (a) and (b) are conflated in the public surface: `litmus.queries`
mixes analytical clients (`RunsQuery`) with a raw store (`EventStore`), and omits two
resources entirely.

## 4. Options

### Option A — Minimal: make `litmus.queries` a consistent, documented entry point (no new classes)
Re-export the read surfaces uniformly and document the two categories. Add `ChannelStore` /
`FileStore` (read methods) to `litmus.queries` alongside the analytical `*Query` classes,
with the module docstring stating plainly: "*Query = analytical (daemon); *Store = direct
resource access." EventStore stays (already there).
- **Pros:** smallest change, no indirection, honest about the two categories.
- **Cons:** the surface still mixes `Query` and `Store` names; doesn't resolve the Run double-read.

### Option B — Full facades: `ChannelQuery` / `FileQuery` / `EventQuery` (the original #3)
Thin read-only facades over each store; demote the `EventStore` re-export to `EventQuery`.
Uniform `*Query` naming everywhere.
- **Pros:** uniform naming; one mental model ("import a `*Query` for any resource").
- **Cons:** three facades that mostly forward to already-clean stores — indirection for
  cosmetic uniformity. Doesn't touch the Run double-read.

### Option C — Also consolidate the Run double-read
On top of A or B: pick ONE run-read surface. Either `RunsQuery` becomes the single public run
reader (and `RunStore` is explicitly the *backend-internal* file layer the `ParquetBackend`
owns — not public), or unify the return types (`RunSummary` vs `RunRow`).
- **Pros:** removes the genuine duplication + shape disagreement.
- **Cons:** `RunStore` and `RunsQuery` serve different *architectural layers* (backend file
  I/O for the save/load + reports path, vs the daemon analytical/UI path) — the split is
  partly justified; collapsing it may couple the backend to the daemon. Needs care.

## 5. Recommendation

**A + C-lite, skip B.** Concretely:
- **Don't build the three thin facades** (B) — they add indirection without function.
  Channel/File/Event reads stay on their stores.
- **Make `litmus.queries` the one consistent, documented entry point** (A): expose every
  resource's read path there with a docstring that names the two categories (analytical
  `*Query` vs direct `*Store`), so there are no "missing" or "reach-in" resources.
- **Clarify the Run split** (C-lite, not full C): document `RunStore` as the
  backend-internal file layer and `RunsQuery` as the public analytical reader; reconcile the
  `RunSummary`/`RunRow` shapes if cheap, but do **not** force-collapse the two layers.

This treats the real problem (inconsistent *public surface*) without manufacturing analytical
classes for resources that have nothing to aggregate.

## 6. Open decisions (yours)

1. **A, B, or C?** (My lean: A + C-lite.)
2. If facades are wanted (B): exact curated read-method set per `*Query`, and lifecycle — the
   `*Query` classes are Flight-connection context managers, but `EventStore` is a
   process-shared singleton; an `EventQuery` would have to reconcile that.
3. Run double-read: leave as-is (documented), reconcile shapes, or fully consolidate?
4. Priority: this is pure consistency — nothing is blocked. Worth doing now, or park it?

## 7. Scope guard

Whatever is chosen, it's a **read-surface** change only — no storage, schema, or write-path
changes. The role/value_type redesign ([`query-by-role-name.md`](query-by-role-name.md)) is
done and independent of this.
