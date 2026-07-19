# Changelog

All notable changes to TesterKit are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0 note: the public API is unstable. Breaking changes are possible in any
0.x release and will be called out in this changelog.

## [Unreleased]

## [0.4.0] - 2026-07-18

**Litmus is now TesterKit.** Releases through 0.3.1 shipped as `litmus-test`;
from 0.4.0 the package is `testerkit`. Version numbering continues unbroken.

### Changed

- **BREAKING** `litmus` → `testerkit` everywhere: PyPI dist, import package, CLI,
  `TESTERKIT_*` env vars, `testerkit.yaml`, agent skills, MCP server. **No aliases.**
  The data-dir key changed too; existing local data is not migrated (pre-1.0).

### Added

- Class-hoisted instrument fixtures hold their reservation for the whole class
  (sequence), not just per method — reentrant, so the lock never drops between methods.
- Brand marks in the operator UI (sidebar wordmark, header mark, favicon).

### Fixed

- Flight daemon thread pools capped (~96 → ~56 per daemon).
- Event export + HTTP/MCP daemon-warm route through the EventStore seam.
- Metrics UI: empty phase filter means ALL phases, not none.
- `testerkit init` merges an existing `.vscode/settings.json` instead of skipping it.
- AI read surface split into the `testerkit-data` skill (no raw parquet).

## [0.3.1] - 2026-07-06

Out-of-the-box: a fresh `testerkit init --starter` runs, surfaces live and finished runs
across every reader, and enables the VS Code Test Explorer with no setup.

### Added

- Live in-flight runs: the runs materializer launches with the pytest session, so a run materializes without a reader. `testerkit runs`, the HTTP API, and MCP tools show in-flight runs as `RUNNING`.
- `testerkit init` enables the VS Code pytest Test Explorer in the generated `.vscode/settings.json`.
- `testerkit init` gains `--no-input` / `--no-ai` for headless scaffolding.

### Changed

- Starter is one vectorized test now: a single inline `testerkit_sweeps` + `testerkit_limits(characteristic=…)` test (`test_output_voltage[3.3]/[5.0]/[5.5]`), no sidecar, spec-driven limit, swept `vin` recorded as an input.
- Starter `testerkit.yaml` drops internal tuning knobs it never sets.

### Fixed

- `testerkit init` no longer hangs in Codespaces/CI (skips the AI-setup prompt under `--no-input`/`--no-ai`/`CI`/`CODESPACES`).
- `testerkit init --starter` instrument assets use `id == role` so calibration joins the station.
- `testerkit validate` detects file types by structural shape — bare `testerkit validate` on a fresh starter used to fail 3 of 6 files.
- Per-test route cleanup no longer requests a fixture during teardown (`PytestRemovedIn10Warning`).
- `testerkit-tests` skill: `observe` is for output evidence, `stream` its live sibling, never an input.
- Stale `psu.yaml`/`dmm.yaml` asset names in `docs/tutorial/quickstart.md`.

## [0.3.0] - 2026-07-06

Execution-grain and schema-versioning release. Steps and vectors get a clean
at-rest grain — a step carries its own measurements; vectors exist only as
condition points (sweeps / inner loops). The at-rest schema starts a distinct
pre-1.0 line (baseline `0.1`) with read-time version dispatch, `slot` becomes
`site` throughout, and instruments gain per-step reservations. (The analytics
suite moves to 0.4.0.)

Pre-1.0: this release rewrites the at-rest schema, and the schema stays on a
0.x line — each minor is a breaking epoch that battle-tests the version
apparatus, so 1.0 is earned later, not frozen now. Regenerate `data/` from
fresh `0.1` artifacts; older parquet is read via version dispatch or quarantined.

### Changed

- **BREAKING** `slot` → `site` everywhere: 0-based `site_index` (always present, default 0) and frozen `site_name`; CLI flags and STDF `SITE_NUM` follow.
- **BREAKING** step executions de-fused — one row per execution, retries counted as occurrences instead of overwriting the prior attempt.
- **BREAKING** `uut_serial` → `uut_serial_number` at rest and in the API.
- **BREAKING** measurement storage reshaped to the step/vector grain — a step carries its own measurements, and per-row values move out of wide per-field columns into three nested `LIST<STRUCT>` columns: `inputs`, `outputs`, `measurements` (one struct per value). Downstream readers of the old flat columns must read the nested columns or query through `MeasurementsQuery`.
- **BREAKING** dynamic input/output fields drop their `in_`/`out_` name prefixes at rest; discovery is role-based (EAV), so a user's unprefixed field names no longer disrupt the query surface. Reference a field by `(role, name)` (`FieldRef`) instead of a prefixed column name.
- **BREAKING** at-rest schema reset to a `0.1` baseline — a distinct pre-1.0 line, not frozen; `parent_path` dropped (derived from `step_path`).
- Timestamps are UTC at every server boundary; clients translate at their own edge (UI / CLI / MCP).

### Added

- At-rest schema versioning: a `0.1` baseline registry, whitelist-dispatch readers at all four store boundaries, and an opt-in forward-migrate sink. Newer-stamped files are deferred (a newer daemon re-reads them); unreadable ones are quarantined — never a hard crash.
- Instrument reservations: re-entrant, timeout-aware resource locks, per-step reserve/release auto-wrap, step-duration server leases, and `instrument.reserved` / `instrument.released` events. Per-step/vector instrument sets are recorded at rest.
- AI-skills reimagined: 11 focused Agent Skills (`testerkit-<domain>/SKILL.md`) installed per-tool by `testerkit setup` (Claude Code/Codex/Cursor/Copilot, native); a new `testerkit docs show` CLI streaming the shipped docs; removal of the old `testerkit refs` CLI / workflows / command stubs.

### Fixed

- Parts page shows run counts and "Observed" rows — observation now keys on the hardware `uut_part_number`, not the config-slug `part_id`.
- Run-detail measurement counts (Overview + Steps tabs) under the reshaped grain.
- Channel-detail live refresh under the default `testerkit serve` (the channels Flight→UI bridge was previously wired only on `--reload`).
- The runs daemon self-heals a corrupt `_index.duckdb` at boot (rebuild from parquet) instead of crash-looping to a blank UI.
- `/explore` defaults its X axis to a per-measurement occurrence `index` and centers a single-valued axis, so non-swept measurements plot instead of showing an empty chart.
- `/explore` no longer emits Quasar "Anchor: target not found" console errors.

## [0.2.1] - 2026-06-26

Improved documentation: a corpus-wide accuracy pass across the reference,
how-to, and operator-UI docs, plus the bug fixes it surfaced.

### Fixed

- Catalog variant `bands:` now append to the base signal instead of replacing it.
- `data_dir` no longer falls back to a hardcoded `./results`; it resolves through `testerkit.yaml`.
- Events filter and Channels list subscribe to `channel.started` (the retired `instrument.read` returned nothing).
- Grafana measurement views and dashboards work against the schema-2.0 layout.
- MCP and skill prompts no longer use a retired event type or invalid sidecar YAML.
- `testerkit setup copilot` writes installer-agnostic `testerkit mcp serve` to `.vscode/mcp.json`.

### Changed

- Measurements (`/explore`) Part/Station/Phase filters use the operator-facing URL keys `part`/`station`/`phase`, matching Metrics. Old `?part_id=` URLs no longer apply.

## [0.2.0] - 2026-06-22

Data-architecture release. A fourth store (FileStore) joins Runs,
Events, Channels for blobs / waveforms / streaming captures. Three
test-author verbs — `observe`, `verify`, `stream` — replace ad-hoc
measurement recording with a typed, routeable surface. The operator UI
gains entity-observed-view across inventory pages, two new pages
(UUTs, Profiles), and an AST-driven Tests rewrite.

A follow-on pass deepens the execution data model (vector-grained,
chronological parquet + EAV), the programmatic read surface (query
measurements by role + name; consistent, typed query/store interfaces
with fool-proof lifecycles), and the quality metrics (Ppk, RTY,
DPMO/DPPM).

### Added

- **FileStore** at `testerkit.data.files` — session-keyed artifact store
  with `files.write(name, value)` returning a `file://` URI. Typed
  serializer registry (bytes, Pydantic, ndarray, Waveform, PIL.Image,
  DataFrame, Arrow IPC) with `register_serializer(...)` for custom
  types and a pickle fallback that warns.
- **Streaming sink** — `files.stream(name, format=...)` for multi-chunk
  captures. Four built-in formats: `raw`, `jsonl`, `tdms`, `h5`.
- **Three test-author verbs.** `observe` records without judgment and
  routes by value shape (scalars/arrays → ChannelStore, blobs →
  FileStore). `verify` records a scalar measurement and judges against
  a limit (non-scalar raises pointing at `observe`). `stream` explicitly
  routes a sample to ChannelStore. All three available as Context
  methods + bare pytest fixtures.
- **Channel start/end events** — `ChannelStarted` / `ChannelEnded`
  bracket every channel session.
- **File start/end events** — `FileStarted` / `FileEnded` bracket
  every streaming sink session. Live consumers range-read the file
  directly via the path in `FileStarted`.
- **Typed event payload columns** — 22 IDs and names (channel_id,
  uut_serial, role, outcome, etc.) promoted from JSON payload to
  typed DuckDB columns, enabling WHERE pushdown. Measured: 2.74×
  speedup on `outcome=failed` filter over 10k events.
- **Live waveform plot** on `/channels/{id}` updates push-style as
  samples arrive.
- **`XYData` model** for paired-array data (IV curves, eye diagrams,
  S-parameter sweeps).
- **Entity-observed-view across operator UI** — stations, parts,
  fixtures, instruments list pages merge YAML-configured + observed-
  in-runs entities with a Configured/Observed chip and filter.
- **New `/uuts` page** — one row per distinct UUT serial in run history.
- **New `/profiles` list + detail pages** — profile registry with
  extends-chain rendering and resolved YAML view.
- **Rewritten `/tests` page** — AST-driven file-level layout with
  per-test panels, run history, and an "Observed in history" section
  for orphaned step paths. Detail page at `/tests/{path}` with Code +
  Sidecar YAML tabs.
- **`/files` operator page** at the DATA STORES nav — list every
  FileStore artifact with mime / size / session filters. Per-artifact
  detail page with mime-switched viewers (image, JSON pretty-print,
  JSONL table, CSV table, NPZ Waveform chart, NPY stats, hex
  download). `?download=1` forces Content-Disposition save.
- **`testerkit_files` MCP tool + `GET /api/files/catalog`** — list
  FileStore artifacts (uri / session / run filters, newest-first) from
  agents and HTTP, mirroring the existing `/files` byte-server.
- **Mock noise spec** — a station `mock_config` value shaped
  `{nominal, sigma}` returns a fresh `random.gauss(nominal, sigma)` draw
  each call, so mock measurements vary run-to-run (real distributions /
  Cpk / yield) instead of one repeated value.
- **Spec-limit overlay on `/explore`** — when the Measurement filter is
  narrowed to one measurement and Y is `measurement_value`, scatter /
  line charts overlay that measurement's low/high limits from its most
  recent run as black dashed lines that track the X axis (a step band
  when the limit is condition-indexed, flat when constant).
- **`/channels` list filters** — Channel ID contains / Type /
  Instrument / Since-Until, URL-mirrored. Live-poll + in-place row
  mutation pattern preserved.
- **`/channels/{id}` chart groups by session** — scalar channels with
  samples from multiple runs render one series per session, distinct
  color per session, legend (scrolling, under the plot) labelled
  `<uut_serial> · <YYYY-MM-DD HH:MM:SS>`. A **Time | Index** x-axis
  toggle (URL-shared via `?x_mode=`) overlays sessions by sample index
  for shape comparison. An activity-driven badge reads `● live` while
  samples arrive, `○ idle` otherwise. Single-session and waveform
  overlays unchanged.
- **`/results/{run_id}` "View this run's" card** — Events, Channels,
  and Files deep-link buttons all carry the run's session into the
  target page (URL-only scoping via `session_filter_banner`).
- **`/launch?test_profile=<name>`** — query param now wires through
  to `LaunchRequest.test_profile` and `--test-profile=` on the pytest
  cmdline. New "Profile" dropdown on the launch form.
- **User-facing API surface re-organization.** 22 names promoted
  across `testerkit` top-level + new `testerkit.queries` submodule + new
  `testerkit.ui` helpers:
  - `from testerkit import connect, observe, verify, stream, Mock, Waveform, XYData, Outcome, TesterKitClient`
  - `from testerkit.queries import RunsQuery, StepsQuery, MeasurementsQuery, EventStore`
  - `from testerkit.ui import page_layout, data_table, subscribe, channel_data, bind_channel_store, ...`

  Deep paths still work; docs / examples / tutorials swept (47
  files) to use the shallow paths. Verbs (`observe` / `verify` /
  `stream`) are now importable functions in addition to the pytest
  fixture form.
- **Examples 08–11** ship four end-to-end demos of the data
  architecture: waveform evidence (`observe(Waveform)` + `verify`
  derived scalars), continuous monitoring (`channels.stream` + live
  UI), artifacts + byte streams (`observe` PIL/bytes/Pydantic +
  `files.stream`), querying data (consumer-side via
  `RunsQuery` / `MeasurementsQuery` / `EventStore`). New tutorial
  steps 11–12 + four how-to pages teach the pattern.
- **Query measurements by role + name.** `MeasurementsQuery` selects via
  `FieldRef` / `FieldRole` (e.g. `parametric(y="v_rail")`),
  scoping to a measurement by role + name instead of the fused wide columns.
- **RTY + DPMO/DPPM on the yield tab.** Rolled throughput yield
  (`∏` per-step first-pass rate), defects-per-million-opportunities
  (opportunity = a measurement, matching the failure-pareto), and
  defective-parts-per-million, surfaced as headline cards + on the CLI /
  MCP / HTTP yield summary. A pooled `MeasurementsQuery.yield_overall`
  computes the headline numbers over the whole filtered set (distinct
  serials once; RTY as the pooled per-step product).
- **`format_number()`** shared UI formatter — `g`-format numeric display
  that strips IEEE float-repr noise (e.g. limits `0.04 – 0.06`, not
  `…0600000000000005`).
- **`scripts/seed-demo-data.py`** — official maintained script that
  generates a representative analytics dataset (multiple parts / serials /
  stations, realistic failures + measurement spread, backdated over a
  multi-day window) so the metrics screenshots and dashboards show real data.
- **Optional-close store contract + lean `Store` Protocol.** `RunStore` /
  `EventStore` / `ChannelStore` / `FileStore` are construct-and-reuse;
  `with` / `close()` are optional, and `EventStore` + `ChannelStore` carry
  `weakref.finalize` nets so a forgotten `close()` can't leak the
  in-process resources.
- **Vector-grained execution model.** `VectorStarted` / `VectorEnded`
  events + retry-aware step events; measurements nested under the vector
  in a chronological-telling parquet with an EAV projection. Observation
  pinning records `uut_pin` on output lanes.

### Changed

- **`testerkit benchmark` reports concurrency per store, measured honestly.**
  The write-throughput sweep (1 → 2 → 4 writers) now runs for **events,
  channels, files, and runs** — each with its own scaling curve and
  per-writer efficiency — instead of a single runs-only sweep collapsed into
  one headline factor (dropped). Two measurement fixes: the round wall is now
  the true overlapped span (`max(end) − min(start)` across workers, not the
  slowest worker's self-timed loop, which overstated throughput); and the
  runs workers no longer perform a synchronous `notify_new_run` the real
  `save_test_run` path never does (that phantom ACK was artificially
  serializing the runs sweep). New `report.json` key `concurrency_by_store`
  (replaces `concurrency_sweep`).
- **Metrics are per-phase now.** The quality dashboards (Yield / Pareto /
  Cpk / Retest) default to **`phase = production`** and the **last 30 days**
  instead of "all phases except development, all time" — production is the
  only phase where FPY / Cpk / pareto are meaningful (development is mock /
  dirty-git data; characterization deliberately drives out-of-spec). The
  Phase filter switches it. The empty state names the cause ("No production
  runs in the window — change the Phase filter or widen the date range")
  rather than the misleading "No measurements yet."
- **BREAKING — `product` → `part` and `dut` → `uut` rename.** The
  type/definition entity is now **Part** and the physical instance is
  now **UUT** (unit under test), aligning with ASAM AoPart / STDF /
  ATML terminology and generalizing beyond electronics. Renamed
  across the YAML schema (`products/` → `parts/`), pytest fixtures,
  event/parquet fields (`uut_serial`, `uut_part_number`), parquet
  columns, operator-UI routes (`/parts`, `/uuts`), and the HTTP / MCP
  query surfaces. Pre-1.0, there is no compatibility shim: existing
  `product_*` / `dut_*` YAML and parquet must be regenerated or
  migrated.
- **Operator-readable session labels everywhere.** Pages that displayed
  session UUID prefixes now resolve to `<uut_serial> · <YYYY-MM-DD
  HH:MM:SS>` via a shared lookup helper (`/channels` data tab,
  `/files` detail Session field, the session filter banner on
  `/events` / `/channels` / `/files`). Banner distinguishes the
  filter-active state (blue) from the session-not-found state (amber)
  so stale bookmarks have explicit copy.
- **Session URL param standardized to `?session_id=`** across
  `/channels`, `/channels/{id}`, `/files`, `/events`, `/results/{run_id}`
  deep-links. Bookmarks using the prior `?session=` form on `/channels`
  no longer carry; deep-link from a fresh `/results/{run_id}` to
  rebuild.
- **`namespace=` parameter parity.** `files.write` and `files.stream`
  now accept `namespace=` matching the existing `channels.write` /
  `channels.stream` / `observe` / `verify` / `stream` surfaces. An
  artifact recorded via `observe(name, value, namespace="psu")` and
  one recorded via `files.write(name, value, namespace="psu")` land at
  the same effective name.
- **`FileStore` serves artifact bytes by URI** via `read(uri)` /
  `read_range(uri, ...)` / `open_input(uri)`. The UI service,
  materializer, `/files-static` route, and HTTP API read through these.
- Channel detail Sequences tab renamed to Capabilities (the tab's
  content was always station capabilities; sequences are deferred).
- ChannelStore schema: `timestamp` → `received_at` (store-side, always
  present) + new nullable `sampled_at` (hardware-side); `samples` →
  `value` (unify scalar/array payload column); `properties`/`attrs` →
  `attributes`; typed leaf-types (`bool`, `int`, `float`, `str` for
  scalar and array shapes).
- Materializer auto-promotion: observation-only vectors now produce
  `DONE` rows in the parquet measurement table instead of disappearing
  from the analytical view.
- Two new operator-UI reference pages (`uuts.md`, `profiles.md`) bring
  the total to 18. Four reference pages updated for the chip + filter.
  Tests reference page rewritten for the AST layout.
- **BREAKING — `Cpk` / `Cp` → `Ppk` / `Pp`.** The capability metric computes
  `STDDEV_SAMP` (overall, long-term σ) — which is Ppk/Pp's basis, not
  within-subgroup Cpk/Cp — so it was renamed for honesty across
  `MeasurementsQuery`, the CLI command, the MCP action, the `/metrics/ppk`
  route, the operator-UI tab, and the docs. True Cpk (within-subgroup /
  I-MR σ) is deferred to v0.3.0.
- **BREAKING — measurements queried by role + name.** The fused
  `out_<name>` / `in_<name>` wide columns are dropped;
  `RunStore.get_measurements` and the run-detail read surface return
  role-split inputs / outputs.
- **BREAKING — channel schema: `data_type` → `value_type`** (cross-store
  datatype-name consistency) and channel `offset` → `sample_offset`. A
  channel's `unit` is unified with the channel descriptor and fails loud
  on conflict (matching `stream`).
- **Scalar `units` → `unit`** across the recording verbs and models
  (collections stay plural); `unit=` is accepted on every recording verb.
- **Consistent query/store interfaces.** Uniform query method names + a
  shared `dynamic_attrs` decoder; typed `Row` returns + `ColumnSchema`
  describe-columns; `FileStore`'s `data_dir` is now the private
  keyword-only `_data_dir` (an infrastructure path, not user-facing).
- `execution/logger.py` → `run_scope.py` (and the `logger` fixture →
  `_run_scope`); `StreamCheckpoint` split into `ChannelCheckpoint` +
  `FileCheckpoint`; store-lifecycle events renamed for grammatical
  consistency.
- The ~3k-line `cli.py` monolith split into a `cli/` package; the System
  Designer page marked experimental. `run_id` is now in the per-run
  parquet filename (prevents silent overwrite).

### Removed

- `InstrumentRead` event class — per-sample events at DAQ rates flooded
  the EventStore. Sample data lives in ChannelStore; start/end events
  (`ChannelStarted` / `ChannelEnded`) replace it.
- `StreamFrameIndex` event class — same flood problem at chunk rate.
  Live consumers read the file directly.
- Dormant measurement-ref struct slot on the run schema (unused).

### Fixed

- **Live-channel UI panels deliver on the event loop.** Channel-data
  callbacks fired on the Flight reader thread, mutating NiceGUI elements
  off the loop; they now marshal through the UI loop (same contract the
  event path already used).
- `/channels/{id}` 500 error on pure-scalar channels.
- **`/explore` default axes pick a real parameter.** A measurement-
  scoped URL with no axis params fell through to the first schema
  column — often an id like `characteristic_id` for X and a stimulus
  input for Y. X now prefers a swept `in_*` input (then `vector_index`
  / time, never an `*_id`); Y prefers `measurement_value`.
- `Context.stream` and `channels.write` did not emit `ChannelStarted`
  (only the observer path did). All writer paths now emit it.
- `/live/{run_id}` Streams panel showed streams from all runs;
  subscription now scopes by `run_id`.
- **`observe()` URIs reach parquet `out_*` columns.** Before this
  fix, `context.observe("uut_photo", img)` wrote the file to
  FileStore but the URI lived only on `Context._observations` —
  `logger.log_measurement` projected `out_*` from
  `vector.observations` (empty), so the operator UI's Measurements
  tab showed no artifacts. Now `observe` mirrors to the active
  vector at write time.
- **Warm-query perf gates stabilized.** Two single-shot perf timers
  flaked under suite load (~30% of full runs). Hard caps (100ms / 200ms)
  unchanged; sampling now takes the
  min over 11 calls so transient spikes don't trip the gate.
- **`StepBuilder` propagates `PASSED` step outcomes** on the default-vector
  (`step.measure()`) path. Previously only `FAILED` propagated, so a passing
  step written via the catch-all `TesterKitClient` results API ended
  `outcome=None` — excluded from step counts and leaving the run outcome
  `None`. Now consistent with the explicit `step.vector()` path.
- **Yield headline cards are pooled.** They previously combined the
  per-(part × station × period) rows, which broke RTY (multiplied as a
  product across groups) and Final Yield (double-counted serials across
  groups, so Final < FPY). `yield_overall` computes them over the whole
  filtered set instead.
- **Daemon write-path resilience.** A daemon killed mid-write reacquires a
  fresh one and resends the un-acked batches (idempotent via
  `ON CONFLICT (id) DO NOTHING`); subscribers resume from their cursor; a
  spawn that times out fails fast and surfaces the crash reason.

### Deferred to v0.3.0

- **Local shared-memory transport** (item 22) — PoC measured 2×
  latency over Flight loopback, not the 3–10× estimated. Revisit on
  symptoms (UI lag traced to transport; >10 kHz capture saturation).
- **Consumer SDK `testerkit.live`** (item 20) — store APIs are
  available; higher-level consumer surface not yet designed.
- **Hardware video encoder formats** (item 23) — `mp4` / `wav` /
  `flac` handlers on top of the streaming-sink machinery.
- **Channels + files as test INPUTS** — current verbs are producer-only;
  a first-class input surface (`channels.read` / `files.read`) lands
  in v0.3.0.
- **Analytics metrics release** — true Cpk (within-subgroup / I-MR σ,
  distinct from the overall-σ Ppk shipped here), per-measurement SPC
  control charts (I-MR / X̄-R + Western Electric rules), yield cross-tab by
  station / fixture / operator, and a generic `pareto(by=measure)`. Roadmap:
  `docs/_internal/explorations/0.3.0-analytics-metrics.md`.

## [0.1.3] - 2026-05-24

Documentation-heavy release. Full per-screen reference for the operator
UI plus a Diátaxis × topic reorg that lands every doc page in a
predictable `<quadrant>/<category>/<topic>.md` cell, with cross-quadrant
"See also" navigation between them.

### Added

- **16 per-screen reference pages** under `docs/reference/operator-ui/`
  — every NiceGUI page (Dashboard, Launch Test, Live monitor, Results
  list/detail, Metrics, Measurements, Events, Channels list/detail,
  System Designer, Stations, Parts, Fixtures, Instruments, Tests)
  documented from the running source. Each carries a cropped
  testid-anchored screenshot.
- **Cropped UI screenshots** via new `scripts/regenerate-ui-screenshots.py`
  (Playwright + headless `testerkit serve`) — manifest-driven, PNGs commit
  into `docs/_assets/operator-ui/`.
- **Tour bridge** at `docs/how-to/overview/operator-ui-tour.md` —
  orientation map of all 14 sidebar entries.
- **Diagnostic how-tos**: `find-flaky-tests`, `compare-runs`,
  `export-results`, `operator-prompts`, plus two MCP-driven recipes
  (`mcp-query-runs`, `mcp-debug-failures`).
- **Grafana integration** reference at `docs/integration/data/grafana.md`
  (pgwire data source + ten shipped dashboards).
- **Live API explorer** subsection in `reference/runtime/api.md`
  covering Swagger UI / ReDoc / OpenAPI JSON.
- **Tutorial step 10** expanded with Results + Metrics walkthroughs.
- Pre-commit hook `screenshot-drift-reminder` — non-blocking nudge to
  rerun the screenshot script when a UI file with a manifest-tracked
  testid changes.

### Changed

- **Docs reorg — Diátaxis × topic matrix.** 64 file moves into
  per-category subdirectories (`overview/` / `configuration/` /
  `execution/` / `data/` / etc.) so the matrix axis is consistent across
  quadrants. Same path tail across quadrants gives natural cross-links:
  `concepts/configuration/fixtures.md` ↔
  `how-to/configuration/configuring-stations.md` ↔
  `reference/configuration.md`. Filename prefixes (`testerkit-`,
  `catalog-`, `why-`) dropped — directory carries the namespace. Three
  former "why-" concept pages rewritten to read as concept references,
  not blog posts.
- **In-app docs viewer** now supports nested subdirectory pages: route
  changed to `{page:path}`, `_parse_section_outline` switched to
  `rglob`, sidebar groups render as accordion (current page's group
  auto-expands).
- **Operator-UI table cleanup**: dropped run UUIDs from operator-facing
  tables (operators identify runs by UUT serial + start time, not
  UUID). Events page Session filter changed from free-text input to
  autocomplete dropdown labelled `<timestamp> • <client>` (pytest /
  jupyter / etc.). Live monitor's Run ID kept but de-emphasized (still
  copyable for URL bookmarking).
- `docs/_assets/` now bundled into the wheel (was missing — operator-UI
  screenshots wouldn't render on `pip install`ed copies).
- Generator path table (`scripts/generate_reference_docs.py`) updated
  for the 5 moved generated reference pages; pre-commit drift-hook
  regex follows.

### Fixed

- **Test race**: a run-status test helper polled for `RunStarted` then
  read partially-materialised steps; it now waits for the run to
  finalise (`ended_at IS NOT NULL`) before reading.
- **System Designer auto-save**: edits to a fixture silently failed to
  save — the save raised an error that was swallowed, so changes weren't
  persisted. Auto-save now writes the fixture correctly.
- Docs viewer's `/docs/_assets/` path was being gobbled by the
  `{page:path}` catch-all and returning HTML instead of PNGs. Mounted
  as static files before the dynamic route.

## [0.1.2] - 2026-05-19

First installable PyPI release. Both 0.1.0 and 0.1.1 wheels shipped without
`testerkit/data/` due to an over-broad `data` exclude pattern in
`pyproject.toml`, so the bundled pytest plugin failed to import on every
fresh install; those releases are yanked.

### Added

- `verify(...)` and `logger.measure(...)` accept a plain dict for `limit=`
  (coerced via `Limit.model_validate`). Tutorials and examples now use the
  dict form; `from testerkit import Limit` stays available for the model object.
- `verify_requires_limit: bool | None` on `ProfileConfig` — set to `False`
  on a characterization profile to route `verify()` to record-only
  semantics when no limit resolves (instead of `MissingLimitError`).
- `testerkit refs list` / `testerkit refs show <topic>` — stream curated reference
  docs (`tiers`, `verify`, `mocks`, `profiles`) to stdout. CLAUDE.md
  templates now point agents at this CLI instead of baking absolute paths.

### Fixed

- Packaging: scoped the `data` exclude pattern in `pyproject.toml` to
  `/data` (top-level only) so `src/testerkit/data/` ships in the wheel.
- Run outcome stamping is now retry-aware. A test that errors on attempt 1
  and passes on the `testerkit_retry` retry stamps the RUN as `passed`
  (matching pytest-rerunfailures, STDF MIR.RTST_COD, and Jenkins flaky-
  test-handler conventions). The errored attempt's step row stays in
  the run for retest / flake analysis.

## [0.1.0] - 2026-04-15

Initial public release on PyPI as `testerkit`.

### Added

- Pytest-native hardware tests — plain `def test_*` functions with
  fixtures and markers for vector expansion, limit checking, measurement
  recording, retries, and mock injection
- Station / fixture / part / sequence YAML configuration, loaded through a
  single store layer with Pydantic validation
- Instrument fixtures resolved from station config (no `conftest.py`
  boilerplate required)
- `--mock-instruments` mode for hardware-free development
- Parquet result storage with per-step instrument traceability
  (serial, cal due date, firmware)
- DuckDB-backed analytics layer over the Parquet silver/gold layout
- Operator UI (`testerkit serve`) built on NiceGUI
- FastAPI HTTP API and MCP server, with parity between the two
- Capability matching (`testerkit_match`) against an instrument catalog
- CLI: `testerkit init`, `discover`, `station init`, `new-test`, `serve`, `runs`,
  `show`, `instrument list`, `mcp serve`, `setup`
- Optional extras for output formats (`stdf`, `hdf5`, `tdms`, `mdf4`),
  transports (`s3`, `gcs`, `azure`, `sftp`), and integrations (`pymeasure`,
  `ni`, `lxi`, `grafana`, `pdf`, `sbom`)

[Unreleased]: https://github.com/pragmatest-dev/testerkit/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/pragmatest-dev/testerkit/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/pragmatest-dev/testerkit/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/pragmatest-dev/testerkit/releases/tag/v0.1.2
[0.1.1]: https://github.com/pragmatest-dev/testerkit/releases/tag/v0.1.1
[0.1.0]: https://github.com/pragmatest-dev/testerkit/releases/tag/v0.1.0
