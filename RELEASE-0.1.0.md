# 0.1.0 Release Checklist

Things that are **hard to change after 0.1.0 ships** because they lock in user
data, user code, or external integrations. Roadmap covers feature work; this
file covers contract surfaces that need a deliberate review *before* the tag.

Items move to "Done" as they land. Items currently on the `ROADMAP.md`
0.1.0 backlog or in memory follow-ups (`project_zero_one_zero_remaining.md`)
are duplicated here for a single source of truth.

---

## Framing

Pre-1.0 stability splits along two lines (full survey + per-surface
analysis in `docs/explorations/api-stability-and-versioning.md`):

- **HARD contracts** — additive-only even pre-1.0. Breaking changes
  affect data on disk; early adopters can't roll back. *Parquet
  artifact + Event WAL.*
- **SOFT contracts** — release-noted breakage acceptable in 0.x.
  Consumers update scripts/agents/clients with notice. *HTTP API +
  CLI + MCP tools.*
- **Internal** — refactor freely. *Python `analysis.*`.*

The 1.0 cut adds the formal versioning infrastructure (path prefixes,
deprecation runways, written stability promises). Pre-1.0, the
absence of that infrastructure is itself the signal that we haven't
locked yet.

## Done

- [x] **Parquet schema discriminator.** Explicit `record_type` column
  (`'run'` / `'step'` / `'measurement'`). Commits `a6df009` + `6a9f363`.
- [x] **Schema version reset.** `SCHEMA_VERSION = "1.0"` (was internal
  4.0). Commit `dff33e6`.
- [x] **Schema migration story documented in code.** Daemon
  `_index.duckdb` tables auto-migrate via `ALTER TABLE ADD COLUMN IF
  NOT EXISTS`; parquet additive evolution via `union_by_name=true`.
- [x] **Inflight measurements** via `LiveRunsSubscriber`.
- [x] **Events DB orphan close.** Sweep emits `RunEnded(aborted)` for
  abandoned runs. Commit `31cf8db`.
- [x] **Unified per-run parquet.** `_steps.parquet` sidecar dropped;
  `RUN_ROW_SCHEMA` is the single canonical shape.
- [x] **Data directory layout: date-partitioned, flat per day.**
  `data/runs/{YYYY-MM-DD}/{timestamp}_{dut_serial}.parquet`.
- [x] **`results_dir` → `data_dir` rename.** Field, function, CLI
  flag, YAML key, on-disk default all renamed (PostgreSQL `PGDATA`
  precedent). First slice of the operator-facing vocabulary sweep.
  Commit `1da7ce2`.
- [x] **Lakehouse interop pattern documented.** Canonical recipes for
  DuckDB, Snowflake, BigQuery, Databricks/Delta, Trino/Iceberg, Pandas/
  Polars in `docs/integration/lakehouse-import.md`.
- [x] **Retry counter naming + base.** 0-based `retry` /
  `vector_retry` / `max_retries`. `retry_count` rollup column on
  `steps_persisted`. Commit `f995cd5`.
- [x] **Event-sourcing rationale documented.** `docs/concepts/why-event-
  sourcing.md` — why TesterKit inverts the usual data model (events
  primary, runs/steps/measurements as projections); the CRUD trap
  it dodges; properties that fall out (replay, time-travel,
  cross-correlation, composable consumers); the principled split
  (config = CRUD, execution = events, channels = streams).
  Commit `ebf77f4`.
- [x] **API stability + versioning framing.** `docs/explorations/api-
  stability-and-versioning.md` — survey of industry patterns
  (Stripe, GitHub, Kubernetes, Iceberg, Delta, Avro, kubectl, Axon,
  Greg Young, semver) applied to TesterKit's six contract surfaces.
  HARD vs SOFT split; 0.1.0 vs 1.0 work bucketing. Commit `ebf77f4`.
- [x] **Public Python API explicit-contract pass — RESOLVED.**
  Decision documented in the API stability doc: `testerkit.analysis.*`
  stays internal for 0.1.0 (already classified internal in
  `docs/audits/public-api.md:66-67`); HTTP/CLI/UI/MCP wrappers are
  the external contract. The Python classes are implementation;
  refactor freely behind the wrappers. No work to ship.
- [x] **Curated docs bundled into the wheel.** `pip install
  testerkit` users now get the user-facing Diátaxis tiers
  (`tutorial`, `integration`, `concepts`, `guides`, `reference`) at
  `testerkit/_docs/` for the in-app `/docs/...` browser. `audits/`,
  `explorations/`, and contributor-only material correctly excluded.
  Commit `092e2ba`.
- [x] **Cut transports + public `EventSubscriber` protocol +
  non-parquet exporters** (the "three stores only" decision).
  Removed ~2200 LoC of public surface; kept `testerkit export` as
  internal CLI machinery (private subscribers + replay). Cloud
  destinations defer to consumer-side recipes in
  `docs/integration/lakehouse-import.md`. Commit `145c89e`.
- [x] **MCP tool-surface review + naming convention.** Reviewed
  the 12 MCP tools, written naming convention into
  `mcp/server.py` module docstring, renamed bare `testerkit` →
  `testerkit_project` for explicit scope. Other 11 names already
  follow the convention (snake_case `testerkit_<verb>` for actions,
  `testerkit_<noun>` for domain-scoped read tools). Commit `27cfbe4`.
- [x] **Catalog schema freeze.** Pinned at `CATALOG_SCHEMA_VERSION
  = "1.0"` in `src/testerkit/models/capability.py`. Additive-only
  evolution within 1.0; rename / removal / type narrowing
  forbidden. `docs/capability-schema.md` declares the freeze
  status. `tests/test_catalog/test_loader.py::TestSchemaVersion`
  enforces the pin — bumping requires a deliberate migration plan.

## Open — Tier 1 (must land before 0.1.0 tag)

- [ ] **Operator-facing vocabulary sweep — continuation.** `@testerkit.test`,
  `testerkit_characteristics`, `testerkit_connections`, `@pytest.mark.
  testerkit_*`. `data_dir` rename was the first slice; one fresh-eyes
  pass on the rest. Effort: medium.

## Open — Tier 2 (good-to-have for 0.1.0; reframed as 1.0-prep work)

- [ ] **`response_model=` coverage on FastAPI endpoints.** Reframed
  per the API stability doc: this is **OpenAPI quality work + 1.0
  prep**, not a stability lock. Doing it now produces a high-quality
  auto-generated `/openapi.json` (consumers can codegen against it)
  and pre-positions us for the 1.0 path-versioning lock without
  committing to it today. Effort: medium.
- [ ] **Mount Swagger UI at `/api/docs`.** `/docs` is taken by the
  NiceGUI Diátaxis browser; FastAPI's auto-doc is currently
  unreachable. Configure FastAPI's `docs_url`, `redoc_url`,
  `openapi_url` to live under `/api/...` instead. Pair with a link
  from `/docs/reference/api` (narrative reference) → `/api/docs`
  (live Swagger UI). Effort: small.
- [ ] **Document the HARD-contract additive promises.** Half a
  paragraph in `docs/concepts/results-storage.md` (parquet) and
  `docs/concepts/event-log.md` (event WAL). Runtime already
  enforces additive-only; this is just writing it down. Effort:
  small.
- [ ] **Docs alignment pass.** Walk `docs/` page-by-page; diff
  each page against current CLI / YAML shape / plugin behavior.
  Mismatch list, no rewrite. Effort: medium.
- [ ] **MCP dry-run.** Connect from an MCP client end-to-end after
  the tool-surface review lands. Effort: small.

---

## Out of scope for 0.1.0

These are real concerns but explicitly deferred:

- **`/api/v1/...` path prefix.** The prefix appearing IS the locking
  ceremony at 1.0; adding it pre-1.0 would imply a stability
  commitment we're explicitly not making. Pre-1.0 routes ship
  unprefixed; consumers see release-note breaks.
- **Formal CLI deprecation policy.** Pre-1.0 the contract is
  release notes. kubectl-style policy ships at the 1.0 cut.
- **Date-based HTTP versioning (Stripe model).** Too much
  infrastructure investment for our scale. Reconsider post-1.0.
- **Per-tool MCP versioning.** Industry hasn't converged. Adopt
  when consensus forms.
- **Upcasting middleware for event evolution.** Build when first
  reshape forces it; likely never within 0.x.
- **Iceberg / Delta as primary storage format.** Big architectural
  lift. Parquet + `union_by_name=true` covers the additive case.
- **Schema migration tooling for breaking changes.** Pre-1.0 with
  no users, breaking changes wipe data. Migration story rebuilds
  at 1.0 when the schema commitment becomes load-bearing.
- **Per-hour data-directory partitioning.** Date-partitioning
  shipped. Per-hour subdivision opt-in if anyone hits the
  flat-within-day wall.
- **Retry forensics at the events layer** (per-execution
  `StepStarted`/`StepEnded` events). Full design parked at
  `docs/explorations/per-execution-step-records.md`. 0.2.0+.
- **Upgrade testing fixture infrastructure.** Starts at 0.2.0
  (need a 0.1.0 frozen starter project to upgrade *from*).
- **Optional extras smoke test for [stdf]/[hdf5]/etc.** Moot once
  the transports + exporters cut lands — those extras evaporate.
