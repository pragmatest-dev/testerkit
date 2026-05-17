# Page audit: docs/how-to/multi-dut-testing.md

**Quadrant:** How-to (multi-DUT / multi-slot testing — sync, slot_id, parallel DUTs)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 3 | 2 |
| Audience | 1 | 2 | 2 |
| Accuracy | 2 | 3 | 2 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 1 | 3 | 5 |
| **Total** | **6** | **18** | **16** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L3 (intro) | The lead paragraph introduces three internal mechanisms (`InstrumentServer`, "subprocess-per-slot architecture", "isolated environment") before the reader has any sense of the user-visible feature. A how-to should open with the operator goal ("run the same tests against N DUTs in parallel from one pytest invocation"), then dive in. The architecture sentence belongs in a later "How it works" block. |
| WARNING | L62 ("Shared Instruments and InstrumentServer") | This section sits between "Serial Assignment" and "Sync Points", but it describes pure platform internals that a reader doing the how-to does not need to do anything with — there are no instructions, only behavior. It interrupts the operator flow (define fixture → run → assign serials → coordinate slots → read results). Move "Shared Instruments and InstrumentServer" to the end as a "How it works" appendix, or fold it into a one-liner where it's first relevant. |
| SUGGESTION | L33 ("Running Multi-DUT Tests") | The CLI command at L37 uses `--dut-serials` but the table that defines `--dut-serials` is below the example. Consider inverting: table first, then example, or annotate each flag inline at first use. |
| SUGGESTION | L74 ("Sync Points") | The `sync` fixture example uses `if sync:` without first telling the reader that `sync` is `None` in single-slot mode — that branch is unexplained at the moment the example appears. The body explanation comes after. Move the "returns None in single-slot mode" note to a line above the example, or as a comment on the `if sync:` line. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L60 | Hedging | "This is useful for development but not recommended for production." (passive recommendation phrasing — say "Use it during development; use `--dut-serials` in production.") |
| WARNING | L66 | Passive voice | "Litmus automatically: 1. Detects shared instrument roles across slots 2. Connects shared instruments once in the orchestrator process" — fine, but the whole "is shared automatically" framing across the section hides the actor. Name the orchestrator process as the actor (the doc already does in places). |
| WARNING | L150 | Hedging | "may need adjustment" ("custom timeouts in `sync.wait()` may need adjustment") — vague. Either state when adjustment is needed or remove. |
| SUGGESTION | L3 | Marketing-adjacent | "subprocess-per-slot architecture" is fine, but "isolated environment" reads as a feature claim without a referent. Either state what is isolated (process memory, env vars, instrument handles) or cut. |
| SUGGESTION | L154 | Hedging | "consider whether instrument access is the bottleneck" — operator phrasing. Replace with a concrete check ("open the execution timeline; look for serialized blocks on a shared role"). |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L3 | Cold drop | "an internal RPC server that lets multiple test workers share one physical instrument" — `InstrumentServer` is introduced parenthetically in the intro before the reader has set up anything. A test engineer doesn't need to know about the RPC server to run the how-to. Pull this to the "Shared Instruments" section. |
| WARNING | L69 | Programmer jargon | "Workers get `RemoteInstrumentProxy` objects for shared roles" — `RemoteInstrumentProxy` is an internal class name leaking into a how-to. Test engineers see `dmm` and `psu`; they don't need the wrapper class name. Reword: "Workers access shared instruments through the orchestrator; the test code still calls `dmm.measure_voltage()` as usual." |
| WARNING | L87 | Programmer jargon | "The `SyncCoordinator` (an internal helper that brokers `sync.wait()` rendezvous between slot workers) in the orchestrator process handles sync point coordination via [EventStore](...) events." — `SyncCoordinator` and "brokers ... rendezvous" are internals. Test engineers care that `sync.wait("name")` blocks until everyone arrives; how that happens is not load-bearing for this how-to. |
| SUGGESTION | L114 | Vocabulary | "Each measurement row includes a `slot_id` column" — fine, but `slot_id` is operator-facing terminology and could be tied to the fixture's slot identifier the operator wrote in YAML (`slot_1`, `slot_2`). State that mapping inline. |
| SUGGESTION | L131 | Internal naming exposed | The env-var table mixes operator vars (`LITMUS_DUT_SERIAL`) with internal-only vars (underscore-prefixed `_LITMUS_*`). Call out that `_`-prefixed names are orchestrator-managed and not for operator use; the table currently presents them all as equally useful. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L9-L29 | The example `dual_board.yaml` defines connections like `vout: { instrument: dmm, instrument_channel: "1" }` — omitting `name:` | `FixtureConnection` declares `name: str` as **required** (`extra="forbid"`, no model_validator populates name from the dict key). Loading this YAML raises Pydantic `Field required` errors for every connection. Verified by running the YAML through `FixtureConfig.model_validate` — produced 4 validation errors. | `src/litmus/models/test_config.py:402-405` |
| CRITICAL | L118 | DuckDB query path `FROM read_parquet('results/**/*.parquet')` | The parquet root subdirectory is `runs/`, not `results/`. On-disk layout is `<data_dir>/runs/{date}/{timestamp}_{serial}.parquet`. All other docs (parquet-schema.md, outputs.md, lakehouse-import.md) use `results/runs/**/*.parquet`. The single-level `results/**/*.parquet` glob will not find files. | `src/litmus/data/run_store.py:52`, `src/litmus/data/backends/parquet.py:181,193-194` |
| WARNING | L48 | Table: `--fixture` ... "(triggers multi-DUT mode)" | `--fixture` triggers orchestrator/multi-DUT mode **only** when the fixture has a `slots:` block (`is_multi_slot`). Single-DUT fixtures (top-level `connections:`) run in single-process mode through the same CLI flag. | `src/litmus/execution/slot_runner.py:355-390` |
| WARNING | L31 | "Slots are executed in definition order (not alphabetical)." | Slots are **spawned** in definition order (dict-insertion order). Once spawned, slot subprocesses run concurrently, so "executed in definition order" misstates the runtime behavior. | `src/litmus/execution/slot_runner.py:221-247` |
| WARNING | L156 | "Orphaned processes: If the orchestrator crashes, worker processes are automatically terminated in the cleanup handler." | Cleanup runs in the orchestrator's `try/finally` block — it only fires for normal exits and handled signals (e.g., SIGINT via `pytest_keyboard_interrupt`). If the orchestrator is `kill -9`'d or segfaults, the `finally` does not run and workers are not cleaned up. Statement is too broad. | `src/litmus/execution/slot_runner.py:295-309` |
| SUGGESTION | L51 | `--mock-instruments` "(each slot gets independent mocks)" | Verified — workers skip the shared-instrument path for mock roles (`continue # Workers get independent mocks`). Phrasing is correct; could add "no state leaks between slots" for clarity. | `src/litmus/execution/slot_runner.py:509` |
| SUGGESTION | L136 | `LITMUS_FIXTURE_SLOT` — "JSON-serialized slot configuration" | Verified — set to `slot.model_dump_json()` of `ResolvedSlot`. Could be stated as `ResolvedSlot.model_dump_json()` for precision, since `ResolvedSlot` is the actual payload (not the raw YAML `FixtureSlot`). | `src/litmus/execution/slot_runner.py:65` |
| VERIFIED | — | 14 additional claims verified against source (env-var names `_LITMUS_SLOT_ID` / `_LITMUS_SESSION_ID` / `_LITMUS_SLOT_COUNT` / `_LITMUS_INSTRUMENT_SERVER` / `_LITMUS_SHARED_ROLES`; `LITMUS_DUT_SERIAL`; `sync` fixture signature `wait(name, timeout=None)`; `SyncPoint` / `SyncCoordinator` / `InstrumentServer` / `RemoteInstrumentProxy` class names; `slot_id` parquet column; `measurement_outcome` / `measurement_value` columns; "Multi-DUT Results" header text; `slot_1: PASS` format; `[slot_id]` stdout prefix; warning text "Single --dut-serial '{x}' applied to all {N} slots"; "Execution Timeline" tab label; per-resource locking semantics; mock independence; FixtureSlot model fields). | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L9-L29 (fixture example) | The example YAML uses `instrument: dmm` and `instrument: psu` without showing or linking to where `dmm` and `psu` are defined (station YAML `instruments:` block). A reader copy-pasting this gets a fixture that references undefined station roles. State the prerequisite ("your station YAML must define `instruments: { dmm: ..., psu: ... }`") and link to configuring-stations. |
| CRITICAL | L37-L42 (Running) | No mention of what files / structure must already exist: a station YAML, a tests directory, and a fixture YAML with `slots:`. A reader landing here from search has no scaffold and no link to one. State the prerequisites at the top of the section. |
| WARNING | L60 (Single serial) | What happens when `--dut-serial` is the default `DUT001` and no `--dut-serials` is given? Does the run prompt? Use that serial for all slots silently? The hooks logic (`if dut_serial == "DUT001": prompt_for_serial`) suggests interactive prompting, which is relevant for multi-DUT operators. |
| WARNING | L74 (Sync Points) | Unstated: what happens if some slots never call `sync.wait("X")` but others do? The example only shows the happy path. Source shows `SyncCoordinator` waits for all `slot_count` arrivals; a slot that never arrives without dying would block forever. State the rule. |
| WARNING | L74 (Sync Points) | Unstated: scope of `sync` is `session`. If a test calls `sync.wait("X")` and another later test also calls `sync.wait("X")`, is "X" reusable? (Source: `_released` set is per-coordinator, so names are one-shot per session.) Operators trying to use the same name across tests will hit silent re-release behavior. |
| WARNING | L150 (Common Issues — slots hang) | "The coordinator auto-unblocks after a slot dies" — but what does the surviving slot's `sync.wait()` return / raise in that case? Source shows it just unblocks (no error). Operators need to know whether they should check for a "partial rendezvous" condition. |
| WARNING | L112 (Parquet Data) | Missing: how do you find the right parquet file for a multi-DUT run? Single-DUT runs are named `{timestamp}_{serial}.parquet`. Multi-DUT runs produce one parquet per slot (per source); state the naming so DuckDB users know how to find them. |
| SUGGESTION | L33 (Running) | No example showing how to run a subset of tests against multi-DUT (e.g., `pytest -k thermal`). Operators wonder whether pytest's selection flags interact correctly with subprocess-per-slot dispatch. |
| SUGGESTION | L62 (Shared Instruments) | The page never tells the reader how to opt out of the InstrumentServer when they want independent instances of a real instrument per slot (e.g., two physical DMMs). State that two distinct instrument roles in the station YAML get independent connections; only same-role references trigger sharing. |
| SUGGESTION | L102 (Execution Timeline) | No "what good looks like" — a screenshot or a textual ASCII description of the Gantt chart would help operators verify their parallel run actually parallelized. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L75 ("Use the `sync` fixture") | First use of the `sync` fixture has no link to its reference entry. `sync` is documented at `docs/reference/litmus-fixtures.md` (line 265, `### \`sync\` — session`). Link as `[sync](../reference/litmus-fixtures.md#sync--session)`. |
| WARNING | L7 | Link `[fixture YAML](../concepts/fixtures.md)` resolves OK, but a how-to about multi-DUT fixtures should link to the **Multi-Slot Fixtures** section specifically (`docs/concepts/fixtures.md` L198, `## Multi-Slot Fixtures`). Use `[fixture YAML](../concepts/fixtures.md#multi-slot-fixtures)`. |
| WARNING | end of page | Missing "See also" section. Sibling how-tos (`spec-driven-testing.md`, `profiles.md`, `context-architecture.md`) all have one. Multi-DUT references obvious related pages with no links: `configuring-stations.md` (station YAML), `reference/litmus-fixtures.md` (sync fixture), `reference/parquet-schema.md` (slot_id column + querying), `reference/cli.md` (`--fixture`, `--dut-serial`, `--dut-serials`, `--mock-instruments` flags), `concepts/fixtures.md` (Multi-Slot Fixtures section), `how-to/mock-mode.md` (mock independence). |
| WARNING | L114 (Parquet Data) | First mention of querying parquet without linking to `reference/parquet-schema.md`. The doc has a whole reference page for the schema; the how-to should send the reader there for column reference instead of teaching schema inline. |
| SUGGESTION | L37 (CLI example) | First use of `--fixture`, `--station`, `--dut-serials`, `--mock-instruments` flags with no link to `reference/cli.md`. At minimum link the table heading. |
| SUGGESTION | L87 | First reference to `EventStore` (already linked via [EventStore](../concepts/event-log.md)) — verified the link resolves to an existing file. |
| SUGGESTION | L131 (Environment Variables) | `_LITMUS_SESSION_ID` and the `session_id` concept could link to `docs/concepts/sessions.md` for readers who don't know what a session is in Litmus. |
| SUGGESTION | L62 (Shared Instruments) | First mention of "instrument roles" with no link. Roles are defined in station config; link `docs/concepts/stations.md` or `docs/how-to/configuring-stations.md`. |
| SUGGESTION | L156 (Common Issues) | `pytest_keyboard_interrupt` handling is implied; could link to `docs/how-to/managing-sessions.md` (or whichever page covers run cleanup / abandoned runs) for the broader cleanup story. |
