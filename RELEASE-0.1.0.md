# 0.1.0 Release Checklist

Things that are **hard to change after 0.1.0 ships** because they lock in user
data, user code, or external integrations. Roadmap covers feature work; this
file covers contract surfaces that need a deliberate review *before* the tag.

Items move to "Done" as they land. Items currently on the `ROADMAP.md`
0.1.0 backlog or in memory follow-ups (`project_zero_one_zero_remaining.md`)
are duplicated here for a single source of truth.

---

## Done

- [x] **Parquet schema discriminator.** Explicit `record_type` column shipped
  (`'measurement'` / `'step'`); every (step, vector) emits a step row;
  measurement-row vs step-row partition is a real column, not implicit
  `measurement_name IS NULL`. Commit `a6df009`.
- [x] **Schema version reset.** `SCHEMA_VERSION = "1.0"` for the public
  release (was internal 4.0). Commit `dff33e6`.
- [x] **Schema migration story documented in code.** Daemon
  `_index.duckdb` tables auto-migrate via `ALTER TABLE ADD COLUMN IF NOT
  EXISTS`; parquet additive evolution flows through `union_by_name=true`
  on read. (`_runs_duckdb_daemon.py:101-102`.) No separate migration tool
  needed for additive changes.
- [x] **Inflight measurements** wired into the `measurements` view via
  `LiveRunsSubscriber`. Live run detail pages see measurements as
  events arrive.
- [x] **Events DB orphan close.** Sweep emits `RunEnded(aborted)` so
  abandoned runs drop out of `events_for_active_runs()` instead of
  accumulating. Commit `31cf8db`.
- [x] **Unified per-run parquet.** `_steps.parquet` sidecar dropped;
  `RUN_ROW_SCHEMA` is the single canonical shape.

## Open — Tier 1 (locks once data or test code exists)

- [ ] **`attempt_count` / `retry_count` decision.** 1-based vs 0-based naming
  open. Daemon-derived rollup column on `steps_persisted`; flows through
  to `StepRow` Pydantic model and `/api/runs/{id}/steps` JSON. Format
  precedent (STDF `RTST_COD`, pytest `--reruns`) leans 0-based; internal
  precedent (`TestVector.attempt`, `vector_attempt`) is 1-based. Pick
  one before the StepRow contract goes public. Effort: small (one
  aggregation column + name decision).

- [ ] **Operator-facing vocabulary sweep.** `@litmus.test`,
  `litmus_characteristics`, `litmus_connections`, `@pytest.mark.litmus_*`,
  CLI flags, YAML field names. One fresh-eyes pass: would a junior test
  engineer read these as natural? Renames after 0.1.0 break every user's
  test files. Effort: medium.

- [ ] **Catalog schema freeze.** Catalog YAML is still being shaped via
  `/catalog-from-datasheet` skill. Pin the shape; stop iterating it.
  Field renames after 0.1.0 break every catalog YAML in user repos.
  Effort: medium.

- [ ] **Results directory layout decision.** Currently flat
  `{timestamp}_{dut_serial}.parquet`. With retention defaulting to
  unlimited, flat directories will hit OS-level walls in production.
  Decision options: stay flat as the contract; date-partitioned
  (`YYYY/MM/DD/...`) opt-in via config; date-partitioned by default.
  Just need the *decision*, not the implementation, before 0.1.0.
  Effort: small (decision + doc note).

- [ ] **Public Python API explicit-contract pass.** `litmus/__init__.py`
  is intentionally empty; deep paths (`from litmus.data.run_store import
  RunStore`) are the contract. Decide if that's the explicit policy and
  document it once. `RunsQuery` / `StepsQuery` / `MeasurementsQuery`
  method names lock the same way. Effort: small.

## Open — Tier 2 (locks once integrations or scripts exist)

- [ ] **`response_model=` coverage on FastAPI endpoints.** Already on
  `ROADMAP.md` 0.1.0 backlog. Without typed responses we either commit
  to JSON shapes by accident or break adopters fixing them later.
  Effort: medium.

- [ ] **MCP dry-run + tool-surface review.** Already on
  `project_zero_one_zero_remaining.md`. `litmus mcp serve`, connect from
  an MCP client, list tools, invoke one end-to-end. Pair with a
  deliberate "is this the tool surface I want forever?" pass — agent
  system prompts will key off these names. Effort: small (verification
  + naming review).

- [ ] **Optional extras smoke test.** Already on
  `project_zero_one_zero_remaining.md`. In a clean venv, install
  `litmus-test[stdf]` / `[hdf5]` / `[grafana]`. Exercise each gated
  feature end-to-end. Reinstall without extras and confirm
  missing-dep error messages point users to the right
  `pip install litmus-test[<extra>]` command. Effort: medium.

- [ ] **Docs alignment pass.** Already on
  `project_zero_one_zero_remaining.md`. Walk `docs/` page-by-page; diff
  each page against current CLI / YAML shape / plugin behavior. Produce
  a list of mismatches (stale flag names, removed concepts, renamed
  fields). No rewrite in this pass — the mismatch list goes to a
  follow-up commit. Effort: medium.

---

## Out of scope for 0.1.0

These are real concerns but explicitly deferred:

- **Schema migration tooling for breaking changes.** Pre-1.0 with no
  users, breaking changes wipe data. Migration story rebuilds at 1.0
  when the schema commitment becomes load-bearing.
- **Date-partitioned results directory implementation** (if the layout
  decision lands as "stay flat for 0.1.0, partition opt-in later").
- **Retry forensics at the events layer** (per-attempt
  `VectorStarted`/`VectorEnded` events). 0.2.0+ if attempt-level
  timing becomes load-bearing.
- **Upgrade testing fixture infrastructure.** Starts at 0.2.0 (need a
  v0.1.0 frozen starter project to upgrade *from*).
