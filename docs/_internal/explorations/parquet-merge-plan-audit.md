# Audit: Parquet-Merge Plan vs What Actually Landed

**Source plan:** "Hardware-test step model: unified parquet, sequences, per-vector rows" — approved 2026-05-06 17:59 UTC, intended to be executed in PR #8 (`feat/unified-step-vector-model` → merged 2026-05-06 19:18 UTC).

**Actual delivery:** PR #8 + commit `a1d1087` covered the events / logger / pytest-plugin / UI layers. **The data-storage layer (schemas, parquet writer, run store, daemon) was not touched.** This document enumerates every section of the plan and marks DONE / PARTIAL / NOT DONE with evidence.

## Summary

| Layer | Status |
|---|---|
| Conceptual model (events, types, identity) | ✓ DONE |
| Logger | ✓ DONE |
| pytest plugin (containers, sweep reordering, indices, manifest) | ✓ DONE |
| UI: step path display, vector_index column, URL tab state | ✓ DONE |
| Data model `StepRow` | ⚠ PARTIAL — `inputs` / `outputs` not added |
| **Schemas** (`schemas.py` — `RUN_ROW_SCHEMA`) | ✗ NOT DONE |
| **ParquetSubscriber** (one parquet per run, drop sidecar) | ✗ NOT DONE |
| **RunStore.notify_new_run** (single path) | ✗ NOT DONE |
| **Runs daemon** (unified ingest, new PK) | ✗ NOT DONE |
| ROADMAP entry for `execution_index` | ✗ NOT DONE |

The events/control plane half of the plan landed; **the data/storage plane half was skipped.** `_steps.parquet` is still written and ingested as a separate sibling file.

---

## Section-by-section

### Plan §1 — Conceptual model (events, types, identity) — ✓ DONE

| Requirement | Status | Evidence |
|---|---|---|
| `step_path` as identity (sequence/method tree path) | ✓ | `events.py` `StepStarted.step_path`; `StepEnded.step_path`; UI uses it (`detail.py:191`) |
| `parent_path` on step events | ✓ | `events.py` `StepStarted.parent_path`; `StepEnded.parent_path` (added by design-review fixes) |
| `step_index` (sequence-relative; resets per class) | ✓ | `_collection_indices.assign_indices` returns it |
| `vector_index` (0-based within sweep) | ✓ | `events.py:329` `StepStarted.vector_index = 0`; `events.py:414` `StepEnded.vector_index` |
| `inputs` (commanded sweep params) on events | ✓ | `events.py:330,378,416` |
| `outputs` (vector-level observations) on events | ✓ | `events.py:379,417` |
| `vector_outcome` on StepEnded | ✓ | `events.py:415` |

### Plan §2 — Schema decision: merge into one parquet — ✗ NOT DONE

> "Today: `_steps.parquet` sidecar + measurement parquet. Two files with nearly identical schemas; the sidecar was a patch for measurement-free steps. **Merge:** one unified parquet per run."

**Status:** Two separate parquets still produced.

| Evidence | Location |
|---|---|
| `_write_steps_parquet` still defined | `parquet.py:783` |
| `_write_results` still calls it | `parquet.py:752` (`self._write_steps_parquet(pq_path, ...)`) |
| Two-file naming convention preserved | `parquet.py:844` (`{stem}_steps.parquet`) |

### Plan §3 — Changes #1: Schemas — ✗ NOT DONE

| Requirement | Status | Evidence |
|---|---|---|
| Unify into `RUN_ROW_SCHEMA` | ✗ | `STEP_SCHEMA` and `MEASUREMENT_SCHEMA` still distinct (`schemas.py:35`, `:115`) |
| Schema fields nullable for measurement-only fields | ✗ | No unified schema exists |
| Step-summary rows distinguishable via `measurement_name IS NULL` | ✗ | n/a — no unified rows |
| `vector_index` on `STEP_SCHEMA` | ✗ | Not in field list `schemas.py:115–174` |
| `parent_path` on `STEP_SCHEMA` | ✗ | Not in field list |
| `inputs` / `outputs` (or `in_*` / `out_*`) on `STEP_SCHEMA` | ✗ | Not in field list |

### Plan §4 — Changes #2: Events — ✓ DONE

| Requirement | Status |
|---|---|
| `StepStarted.vector_index` | ✓ |
| `StepStarted.inputs` | ✓ |
| `StepEnded.vector_index` | ✓ |
| `StepEnded.vector_outcome` | ✓ |
| `StepEnded.outputs` | ✓ |
| `StepEnded.inputs` | ✓ |
| `StepEnded.parent_path` | ✓ (added in design-review pass) |

### Plan §5 — Changes #3: Logger — ✓ DONE

| Requirement | Status | Evidence |
|---|---|---|
| `start_step(... step_index=, vector_index=, inputs=, ...)` | ✓ | `logger.py` accepts kwargs and emits StepStarted with them |
| Emit `StepStarted` with `vector_index` + `inputs` | ✓ | |
| Emit `StepEnded` with `vector_index` + `vector_outcome` + `outputs` + `inputs` | ✓ | |
| `_json_safe` coercion of vector params | ✓ | extracted to `litmus.data._json_safe` in design review |

### Plan §6 — Changes #4: pytest plugin — ✓ DONE

#### §6a Log class as container step — ✓ DONE
- `_ensure_class_container` opens/closes container on class transition (`hooks.py:980`)
- `_close_open_class_container` runs at session finish (`hooks.py:703`)
- Container outcome cascades from children via `_stamp_container_outcome` (added in design-review pass)

#### §6b Collection reordering for class-level sweeps — ✓ DONE
- `_has_class_level_sweep` (`hooks.py:438`)
- `_reorder_class_sweep_items` (`hooks.py:469`)
- Called from `pytest_collection_modifyitems` (`hooks.py:429`)

#### §6c Planned vector count on manifest — ✓ DONE
- `CollectedItem.vector_count_planned` (`hooks.py:563`)
- Computed by `_collection_indices.assign_indices`

#### §6d Sequence-relative step_index + vector_index — ✓ DONE
- `_collection_indices.assign_indices(keys)` returns the triple `(step_index, vector_index, vector_count_planned)` per `(module, class, function)` key
- Wired into `pytest_runtest_call` via `start_step(step_index=, vector_index=, ...)`

### Plan §7 — Changes #5: ParquetSubscriber — ✗ NOT DONE

> "**`_write()` writes ONE parquet per run instead of two.** Rows include: 1. Measurement rows ... 2. Step-summary rows for vectors with no measurements ... 3. Container step rows ... 4. Unrun-step rows ... Drop `_write_steps_parquet`."

| Requirement | Status | Evidence |
|---|---|---|
| `_write` emits one unified parquet per run | ✗ | Still emits `*.parquet` + `*_steps.parquet` (`parquet.py:752`) |
| Measurement rows in unified output | ⚠ | Already in measurements parquet, just not unified with step rows |
| Step-summary rows for measurement-free vectors | ✗ | Step rows live in sibling file with separate schema |
| Container step rows | ✗ | No row emitted for class containers in current writer |
| **Unrun-step rows** from manifest | ✗ | Not implemented |
| Drop `_write_steps_parquet` | ✗ | Function still at `parquet.py:783` |
| Single `atomic_write_table` per run | ✗ | Two writes (`parquet.py:264` measurements; `parquet.py:845` steps) |

### Plan §8 — Changes #6: RunStore.notify_new_run — ✗ NOT DONE

> "Single path to the unified parquet (the `_steps.parquet` arg goes away)."

| Evidence | Location |
|---|---|
| `notify_new_run` still derives steps_path from measurements path | `run_store.py:335` (`steps_path = parquet_path.with_name(parquet_path.stem + "_steps.parquet")`) |
| Still passes both paths to daemon | `run_store.py:336–337` |

### Plan §9 — Changes #7: Daemon — ✗ NOT DONE

> "Unified ingestion ... `steps_persisted` PK becomes `(run_id, step_path, vector_index)` — per-vector rows preserve outcomes for measurement-free steps."

| Requirement | Status | Evidence |
|---|---|---|
| Unified ingest path (no `_is_steps_file` split) | ✗ | `_is_steps_file` still at `_runs_duckdb_daemon.py:426`; `_ingest_one_file` branches on it (`:1071–1074`) |
| Single ingest insert pattern | ✗ | `_index_steps_file` (`:1101`) vs `_index_parquet_file` (`:1115`) still separate |
| `steps_persisted` PK = `(run_id, step_path, vector_index)` | ✗ | Current PK is `(run_id, step_index)` (`_runs_duckdb_daemon.py:171`) |
| `steps_persisted` has `vector_index` column | ✓ partial | Column exists at `:255`, but not part of PK |
| Idempotent migration via `ALTER TABLE ADD COLUMN IF NOT EXISTS` | ✗ | No new column migration; `vector_index` was already there |
| `runs_persisted` aggregates from `steps_persisted` | ⚠ | Already does today; no change required for plan goal |

### Plan §10 — Changes #8: Data model (StepRow) — ⚠ PARTIAL

| Requirement | Status | Evidence |
|---|---|---|
| `StepRow.vector_index: int = 0` | ✓ | `steps_query.py:46` (`vector_index: int | None = None`) |
| `StepRow.parent_path` | ✓ | `steps_query.py:42` |
| `StepRow.step_path` | ✓ | `steps_query.py:41` (already existed) |
| `StepRow.inputs: dict[str, Any]` | ✗ | Not in StepRow |
| `StepRow.outputs: dict[str, Any]` | ✗ | Not in StepRow |

### Plan §11 — Changes #9: UI — ⚠ PARTIAL

#### §11a Steps table

| Requirement | Status | Evidence |
|---|---|---|
| Steps table uses `step_path` (or visual tree) | ✓ | `detail.py` step rows display `step_path or step_name` |
| `vector_index` column visible | ✓ | `detail.py:389–393` (column definition) |
| Surface `inputs` / `outputs` per vector row | ✗ | Not surfaced; StepRow doesn't carry them, table doesn't show them |

#### §11b URL tab state — ✓ DONE

| Requirement | Status | Evidence |
|---|---|---|
| `tab` query parameter on `result_detail_page` | ✓ | `detail.py:46` (`async def result_detail_page(run_id: str, tab: str = "")`) |
| Initial tab restored from URL | ✓ | `detail.py:191` (`initial_tab = _tab_lookup.get(tab, overview_tab)`) |
| `push_url_state` on tab change | ✓ | `detail.py:240–243` inside `_on_tab_change` |
| Wired via `tabs.on_value_change` | ✓ | `detail.py:249` |

### Plan §12 — ROADMAP entry for `execution_index` — ✗ NOT DONE

> "`execution_index` (global pre-order traversal counter) → ROADMAP. Today `started_at` is sufficient for total ordering."

`grep "execution_index" ROADMAP.md` returns no matches.

---

## Concrete remaining work (TODO list to land the plan)

In rough dependency order:

1. **Add to `STEP_SCHEMA`** the four missing fields: `vector_index`, `parent_path`, `inputs`/`in_*`, `outputs`/`out_*`. Or — better — go straight to the unified `RUN_ROW_SCHEMA`.

2. **Define `RUN_ROW_SCHEMA`** in `schemas.py` covering both measurement and step row variants. Step-summary rows have `measurement_name IS NULL`. Required: run/session identity, `step_path`, `vector_index`, `parent_path`, step context, run context. Nullable: measurement-only fields.

3. **Rewrite `ParquetSubscriber._write` / `_write_results`** to emit one parquet per run. Rows:
   - One per recorded measurement (existing measurement-row content, plus `vector_index`, `parent_path`)
   - One per `(step_path, vector_index)` that ran but has no measurements (step-summary row; `measurement_name IS NULL`)
   - One per class container (`parent_path = ""`, `step_path = class_name`, `measurement_name IS NULL`)
   - One per planned-but-unrun `(step_path, vector_index)` from the manifest (`outcome IS NULL` or sentinel)

4. **Drop `_write_steps_parquet`** and any helpers only used by it.

5. **Update `RunStore.notify_new_run`** to take a single path (the unified parquet). Update all call sites.

6. **Refactor `_runs_duckdb_daemon`**:
   - Drop `_is_steps_file`, `_index_steps_file`, `_index_parquet_file` — replace with a single ingest path that reads the unified parquet and routes rows to `runs_persisted` / `steps_persisted` / `measurements_persisted` based on `measurement_name IS NULL`.
   - Change `steps_persisted` PK from `(run_id, step_index)` to `(run_id, step_path, vector_index)`.
   - Idempotent migration of the existing DuckDB index — bump schema version or ALTER + rebuild.

7. **Update `StepRow`** in `analysis/steps_query.py` to add `inputs: dict[str, Any]` and `outputs: dict[str, Any]` (`in_*` / `out_*` dynamic columns or a single dict each).

8. **Update steps query** to populate `inputs` / `outputs` from the unified rows (group by `(run_id, step_path, vector_index)`).

9. **UI step row** (`detail.py`): surface `inputs` / `outputs` per vector row when present (e.g., expandable detail).

10. **ROADMAP.md**: add `execution_index` (global pre-order counter) under future work.

11. **Wipe existing results** (pre-1.0, no users — plan explicitly approves) so DuckDB schema migrations have a clean slate.

12. **Verification** per the plan:
    ```bash
    rm -rf results/
    cd examples/05-product-spec && uv run pytest -s
    # Validate: container row exists; sweep step has 4 vector rows; sequence runs once per condition
    uv run pytest -q
    ```

13. **Test coverage** for unified parquet: read-back of measurement rows + step-summary rows + container rows + unrun rows; PK preservation in `steps_persisted`; round-trip of `inputs`/`outputs`.

## What this changes about the ideal-data-architecture exploration doc

The Delta-vs-bare-parquet "directory layout collision" I described in the discussion (split `runs/measurements/` and `runs/steps/`) **disappears once this plan lands**: there will be one parquet per run, no sibling. So the path forward for OD-3 (Delta adoption) becomes simpler — wrap a single set of parquets in `_delta_log/` instead of two.

The "two writers, two code paths" structural observation also **partially collapses**: there's still the producer-vs-daemon writer split (only one writer total per run, but the split between happy-path and orphan-path remains). That's OD-1, distinct from this plan.
