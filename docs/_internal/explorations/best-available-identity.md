# Observed-Entity Identity, Reconciliation & Write-Path Single-Sourcing — Design Contract

**Status:** proposed (awaiting final review)
**Date:** 2026-07-03
**Branch:** `feat/0.3.0-grain-reshape`
**Origin:** Parts page "Observed" rows never render (0.3.0 UI smoke test, task #59). Diagnosis widened into an identity-model problem, and under it, a write-path single-sourcing problem.

---

## 1. Symptom

The Parts config page (`/parts`) shows configured parts with **0 run counts** and renders **no `observed_only` rows**, regardless of run history.

## 2. Root cause — two layers

The Parts bug is a **read-side** defect, but it sits on top of a **write-side** one. Both must be named; the read-side fix is capped by the write-side.

**Layer 1 (read / observation):** the entity pages group observation on the wrong identity axis — a config-slug abstraction instead of the hardware-observed identity — and the guard/error-handling hid it.

1. **Wrong identity axis.** `parts_with_provenance` group-bys `part_id`; stations `station_id`; fixtures `fixture_id`; instruments `instrument_id` (`services.py:214, 398, 1111, 586`). These are *logical/config* ids. The rest of the UI reports on the **hardware** identity (`uut_part_number` / `station_hostname`).
2. **Hand-maintained allow-list drift.** `usage_stats(by)` checks `_VALID_USAGE_STATS_COLUMNS` (`runs_query.py:42`); `part_id` is absent (comment cites `feedback_operator_facing_identifiers.md`, a memory file that **no longer exists**), so `usage_stats("part_id")` raises `ValueError` before any SQL. The frozenset duplicates the canonical `_RUNS_PERSISTED_COLUMNS` (`_runs_duckdb_daemon.py:505`), reachable via `describe_columns()`.
3. **Swallowed failure.** `usage_stats_by` catches `ValueError` → `{}` (`services.py:1501`); `_instrument_id_usage_stats` catches `(ValueError, Exception)` → `{}` (`services.py:609`). A blocked column looks identical to "no data."

**Layer 2 (write / capture):** observation can only ever surface what the run *carried*, and the write paths disagree on what they capture.

- **Two live write paths, diverged.** The pytest plugin emits events → accumulator → parquet + daemon index. The **client** (`LitmusClient`/`RunBuilder`) hand-builds a `TestRun` and calls `save_test_run` **directly** (`client.py:345`), bypassing `build_run_metadata`/`RunScope` — the shared assembly that fills the *complete, uniform* field set. `save_test_run`'s only callers are `client.py` and `benchmark/*` (verified) — not pytest.
- **The client captures only what `start_run`'s signature accepts** — `uut_serial`, `station_id`, `uut_part_number`, `station_type`, `operator`, `test_phase`. No `part_id`, no `fixture_id`, no instrument records, **no `station_hostname`**, no environment, no `project_name`. So every non-pytest run is identity-poor by construction.
- **No HTTP ingest door.** The only `POST /runs` is a *launch* endpoint (`api/app.py:424`, `RunLaunchResponse`), not result ingest. Writing has no wire entry point; external tools must import the Python client.
- The demo-data seeder rides this same client path — a maintained surface already drifting (prior fixes landed on this branch: default-vector removal, `save_test_run` struct drift, `aggregate_run_stats` count bug).

`part_id` exists in parquet (`schemas.py:180`), the daemon (`:524`), and is written when set (`_row_helpers.py:477`) — it is simply the wrong axis *and* usually null on client runs. That "usually null" is the Layer-2 shadow of the Layer-1 bug.

## 3. Why observation exists

The data dir is **global and shared across repos**. A UI in repo B has only repo B's configs but sees runs from repo A. So "Observed" must be built from **facts carried in the run itself**, meaningful to a viewer holding none of the producing repo's config — standard **asset/inventory reconciliation** ([CloudQuery](https://www.cloudquery.io/learning-center/cloud-observability-pillars-technologies-and-practices), [USPTO](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10282426)). That dictates the identity: the **hardware-observed** identity, not a config back-reference.

## 4. Layer 1 — identity model: hardware id at the entity's grain

Every entity has a **hardware-observed unique identifier at its natural grain**, present in both observed runs and (as the same value) in configured YAML. That is the group/join key. Config abstractions (`part_id`, `station_id`, `role`, `resource`, `instrument_id`) are **secondary attributes** — display and richer reconciliation, never the group key. This is the entity-resolution / golden-record principle: **keep the stable hardware identifier as the key; names, aliases, slugs are attributes** ([Practical Data Modeling](https://practicaldatamodeling.substack.com/p/entities-instances-and-identifiers-b5f), [MS Learn](https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/power-platform/entity-modeling)).

| Page | Grain | Hardware unique id (group key) | Config abstraction (secondary) | Observed↔config join | Verified |
|---|---|---|---|---|---|
| **Parts** | type | `uut_part_number` | `part_id` | `uut_part_number` == config `part_number` | ✅ `metadata.py:74-75` |
| **Instruments** | unit | `serial_number` | `role`, `instrument_id`, `resource` | `serial_number` == asset serial | ✅ observed carries serial (`_runs_duckdb_daemon.py:1185`) |
| **Stations** | machine | `hostname` | `station_id` | `hostname`/`id`/`name` | ⚠️ `StationConfig.hostname` optional (`station.py:66`); needs multi-key reconcile + write-side capture |
| (UUTs, if surfaced) | unit | `uut_serial_number` | — | — | — |

A part is a **type** (one `part_number`, many units); an instrument and a UUT are **units** (each `serial_number` is one physical thing); a station is a **machine**.

## 5. Layer 1 — how grouping works: group-by, not a graph

A single hardware key present on both sides means grouping is a plain `GROUP BY` — **no union-find / identity graph** (an earlier draft's over-engineering; the sparse-record premise doesn't hold — the hardware key is deliberately populated).

1. **Group observed** by hardware key (`GROUP BY uut_part_number` / `serial_number` / `hostname`) — heavy aggregation in SQL, scales with runs.
2. **Reconcile to config**: `configured` iff the entity's hardware key matches a configured record's declared identifier, else `observed_only`. Parts/Instruments: the group key *is* the config identifier (one-key join). Stations: the observed key (`hostname`) may differ from the config's declared id (hostname optional), so reconcile on `hostname` OR `station_id` OR `name` — a config-count-bounded match, not a run-level graph.
3. **Survivorship for the display label only**: one canonical label per entity. The `COALESCE(..., 'unknown')` in analytics is a **display-label fallback**, confined to presentation — never a group/join key (unstable; changes with which fields are present). The metrics queries `GROUP BY` the coalesced label — acceptable for a chart's "unknown" bar, wrong for an entity roster.

**Junk-id exclusion:** null / sentinel / non-discriminating identifiers (`unknown`, empty, `localhost`, an unreported serial) are excluded from grouping/matching, or distinct entities over-merge — the entity-resolution precision knob ([Data Ladder](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/), [Profisee](https://profisee.com/blog/mdm-survivorship/)).

## 6. Layer 2 — write side: single-source the run

Observation completeness is **capped by capture.** Fixtures, instruments, `part_id`, `hostname`, environment are absent from client runs because the client doesn't go through the shared assembly. The durable fix is one definition of "a complete run":

- **Route the client through `build_run_metadata`** (the same assembly pytest uses), so every runner emits the full, uniform field set instead of `start_run`'s subset. This closes `part_id` / `fixture_id` / instrument / `hostname` / environment / `project_name` capture **at the source**, and makes fixtures/instruments observation work for non-pytest runs.
- **Consider an HTTP ingest endpoint** so the client (and LabVIEW/TestStand) become thin wire clients over one canonical write path, rather than importing Python and writing parquet directly. (Adjacent to the parked req-6 serving-tier swap.)
- **Interim point-fix:** the one Layer-2 capture Layer-1 needs immediately is `station_hostname` — the client should grab `socket.gethostname()` (trivial, physical, universally correct) so `hostname` is the universal station key. This is a subset of the full single-sourcing work and can land with the identity model without waiting for it.

Until Layer 2 lands, Layer 1 is honest but partial: parts/stations observe correctly (their hardware keys *are* captured), instruments observe for pytest runs, fixtures stay config-scoped.

## 7. Guard & error-handling fixes (independent)

- **Schema-derived guard:** replace `_VALID_USAGE_STATS_COLUMNS` with validation against real `runs` columns (`_RUNS_PERSISTED_COLUMNS` / `DESCRIBE`). Injection stays blocked; "forgot a real column" is eliminated. Operator-facing *pareto dimension curation* stays a separate presentation list.
- **No silent swallow:** an invalid group-by is a **test-caught programming error**, not `return {}`.

## 8. Per-entity status (verified)

| Entity | Current group key | State | Under this contract |
|---|---|---|---|
| **Parts** | `part_id` | hard-broken (invalid col → swallowed → empty) | `uut_part_number` |
| **Stations** | `station_id` | works but off-axis from the hostname-based UI | `hostname` (+ client capture + multi-key reconcile) |
| **Instruments** | `instrument_id` | groups on a logical/role-like axis; risks fusing distinct instruments | `serial_number` |
| **Fixtures** | `fixture_id` | pytest-only; no hardware identity exists | stays `fixture_id`; client-visibility needs Layer 2 |

## 9. Non-goals & caveats

- **Doc drift:** `TestRun` docstring (`models.py:472-474`) claims `station_hostname` "always populates from `socket.gethostname()`." False — defaults `None`, only `RunScope` fills it. Fix with this work.
- **Instrument null-serial (open decision):** serial may be null for gear that never reports one → fall back `serial → resource` (survivorship) or show as unidentified? Decide when instruments are worked (Phase 1).
- **Fixtures have no hardware identity** — config-scoped until Layer 2.
- **Metrics `GROUP BY` on coalesced label** — acceptable-for-charts shortcut, not rewritten.
- **Benchmark write path** (`benchmark/*` → `save_test_run`) is deliberate perf scaffold; out of scope for single-sourcing.

## 10. Scope & sequencing

Three phases; each independently shippable, each capped by the prior for completeness.

- **Phase 0 — unblock 0.3.0 (#59). ✅ DONE (2026-07-03).** Rekeyed `parts_with_provenance` onto `uut_part_number` (join configured via `Part.part_number`, now surfaced from `discover_parts`); observed-only rows identified by the part number. `uut_part_number` was already a valid column — no guard rework needed. Surface: `services.py` + `test_parts_provenance.py` (16 provenance tests green, ruff clean, no new mypy).
- **Phase 1 — full read-side identity model (own item, near #57).** All entity pages on hardware-id-at-grain; schema-derived guard; swallow → test-caught error; stations multi-key reconcile; junk-id exclusion; the `station_hostname` client point-fix; docstring fix; settle instrument null-serial. Touches daemon guard, ~3 service functions, `client.py` (hostname only), tests.
- **Phase 2 — write-side single-sourcing (own item, #58 / dedicated).** Client routed through `build_run_metadata`; complete uniform capture at the source; fixtures/instruments observation for non-pytest; consider HTTP ingest + thin wire client. The parked "single-sourcing" thread, now scoped.

**Recommendation:** Phase 0 now (it *is* the bug); Phase 1 and Phase 2 as their own reviewed items. Do not fold Phase 2's client change into a bug-fix slot.

---

## Progress log

- 2026-07-03 — Contract drafted from #59, reshaped twice. Final model: **two layers.** Read side = hardware unique id at the entity's grain (parts→`uut_part_number`/type, instruments→`serial_number`/unit, stations→`hostname`/machine), config slugs demoted, grouping by `GROUP BY` (no union-find), coalesce = display-label survivorship only, schema-derived guard, no-swallow. Write side = single-source the client through `build_run_metadata` (it currently bypasses it via `save_test_run`, `client.py:345`), closing capture gaps at the source; interim `station_hostname` client point-fix. Sequenced Phase 0/1/2. All read- and write-side claims verified against source this session. Open: instrument null-serial fallback.
- 2026-07-03 — **Phase 0 landed.** `parts_with_provenance` rekeyed onto `uut_part_number`; `discover_parts` now surfaces `part_number`; observed-only parts identified by part number. `test_parts_provenance.py` updated to the part_number join; 16 provenance tests pass, ruff clean, no new mypy. Phases 1 (full read-side model) + 2 (write-side single-sourcing) remain — tracked as their own follow-on task.
