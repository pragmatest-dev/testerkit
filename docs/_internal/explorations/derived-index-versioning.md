# Derived-index versioning — fingerprint + rebuild-on-mismatch (#47)

**Status:** design contract (approved 2026-07-03, folded into 0.3.0). Part of #53/#47.
**Scope tonight:** the safety MVP only. The per-table cost-ladder is a documented fast-follow, NOT tonight.

---

## Why

The `_index.duckdb` is a **derived cache**, versioned by *three* independent things (prior art: event-sourcing upcasting / schema registry; dbt manifest / event-sourcing projection rebuild; Lucene codec):

| Axis | Trigger | Handled by |
|---|---|---|
| at-rest schema (`schema_version`) | parquet format changed | `schema_dispatch` (exists) |
| **projection definition** (DDL shape) | daemon's tables/views changed | **THIS — the gap** |
| engine storage format (`.duckdb`) | DuckDB unreadable | `_open_index` corrupt self-heal (#59, exists) |

The axes are **nested**: the projection is downstream of at-rest, so an at-rest change *forces* a projection rebuild, but a projection-only change rebuilds within the same at-rest version. Today's snowflake was a projection-only change; the self-heal only catches *unreadable* files, so a **readable-but-wrong-shape** stale index (old projection, valid parquet) is currently undetected — it would silently serve the old shape / error on new views. This closes that.

## MVP (tonight) — fingerprint + rebuild-on-mismatch

1. **Projection fingerprint.** A deterministic hash of the DDL the daemon runs in `_ensure_schema` + `_create_views` (the `CREATE TABLE` / `CREATE OR REPLACE VIEW` statement text, normalized for whitespace). It auto-detects *any* projection change — nothing to bump by hand. Single-source it (one function `_projection_fingerprint()` that hashes the exact DDL strings the daemon executes, so it can't drift from the real schema).
2. **Stamp.** On index build, store `(schema_version, projection_fingerprint)` in an `_index_meta` table in the `_index.duckdb`.
3. **Boot check.** In `_open_index` (alongside the existing corrupt-file probe): read the stored stamp. If `schema_version` differs OR `projection_fingerprint` differs from the current code → **discard + rebuild** (return `is_fresh=True`, which the caller's cold-start ingest already handles). If both match → open normally (`is_fresh=False`). Missing `_index_meta` (pre-this-change index) → treat as mismatch → rebuild.
4. This **extends** the existing self-heal path — corrupt → rebuild AND stale-shape → rebuild both funnel into the same `is_fresh=True` cold-start.

### Guardrails
- Derived-cache ONLY — no at-rest / `RUN_ROW_SCHEMA` / `schema_version` change. The at-rest stays 0.1.
- The fingerprint MUST be deterministic and stable across re-open of an unchanged daemon (same DDL → same hash). Normalize whitespace; don't hash volatile things (timestamps, paths).
- First version rebuilds the WHOLE index on mismatch — safe; a projection change is rare and the rebuild is off the critical path (background, from parquet).

### Tests
- Stamp match → `is_fresh=False` (no rebuild). Stamp mismatch (hand-write a wrong fingerprint into `_index_meta`) → `is_fresh=True` (rebuild), and the reopened index is correctly the current shape + empty (cold-start). Missing `_index_meta` → rebuild.
- Fingerprint is stable: `_projection_fingerprint()` == itself across two calls; changes iff the DDL changes (add a column to a CREATE → different hash).
- Full pre-commit suite green.

## Fast-follow (NOT tonight) — the cost ladder

Per-table rebuild instead of whole-index (prior art: dbt `state:modified` rebuilds only changed models; Iceberg additive = metadata-only; ES additive-field = free):
- view-only diff → `CREATE OR REPLACE VIEW` (free)
- additive column → `ALTER TABLE ADD COLUMN` (metadata-only)
- structural change → re-read parquet for **only the changed table**, blue-green if online
Requires per-table fingerprints. Deferred; the MVP's whole-rebuild is correct, just less efficient.

## Open (verify before doctrine)
Two thin spots in the prior-art grounding, to web-confirm and cite later (not code blockers): (a) whether anyone runs DDL-fingerprint-triggered rebuilds on an *embedded analytical cache* specifically, (b) the engine-format axis (Lucene-codec analogy). The mechanism is safe regardless — worst case it rebuilds unnecessarily.

---

## Progress log
- 2026-07-03 — Design approved; folded into 0.3.0 (Friday low-traffic window). MVP = fingerprint + rebuild-on-mismatch extending the `_open_index` self-heal; cost-ladder deferred. Delegated to Sonnet.
