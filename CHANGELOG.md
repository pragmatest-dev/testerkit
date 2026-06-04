# Changelog

All notable changes to Litmus are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0 note: the public API is unstable. Breaking changes are possible in any
0.x release and will be called out in this changelog.

## [Unreleased]

Data-architecture release. A fourth store (FileStore) joins Runs,
Events, Channels for blobs / waveforms / streaming captures. Three
test-author verbs — `observe`, `verify`, `stream` — replace ad-hoc
measurement recording with a typed, routeable surface. The operator UI
gains entity-observed-view across inventory pages, two new pages
(DUTs, Profiles), and an AST-driven Tests rewrite.

### Added

- **FileStore** at `litmus.data.files` — session-keyed artifact store
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
- **Channel lifecycle events** — `ChannelStarted` / `ChannelClosed`
  bracket every channel session.
- **Stream lifecycle events** — `StreamStarted` / `StreamEnded` bracket
  every streaming sink session. Live consumers range-read the file
  directly via the path in `StreamStarted`.
- **Typed event payload columns** — 22 IDs and names (channel_id,
  dut_serial, role, outcome, etc.) promoted from JSON payload to
  typed DuckDB columns, enabling WHERE pushdown. Measured: 2.74×
  speedup on `outcome=failed` filter over 10k events.
- **Live waveform plot** on `/channels/{id}` updates push-style as
  samples arrive.
- **`XYData` model** for paired-array data (IV curves, eye diagrams,
  S-parameter sweeps).
- **Entity-observed-view across operator UI** — stations, products,
  fixtures, instruments list pages merge YAML-configured + observed-
  in-runs entities with a Configured/Observed chip and filter.
- **New `/duts` page** — one row per distinct DUT serial in run history.
- **New `/profiles` list + detail pages** — profile registry with
  extends-chain rendering and resolved YAML view.
- **Rewritten `/tests` page** — AST-driven file-level layout with
  per-test panels, run history, and an "Observed in history" section
  for orphaned step paths. Detail page at `/tests/{path}` with Code +
  Sidecar YAML tabs.

### Changed

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
- Two new operator-UI reference pages (`duts.md`, `profiles.md`) bring
  the total to 18. Four reference pages updated for the chip + filter.
  Tests reference page rewritten for the AST layout.

### Removed

- `InstrumentRead` event class — per-sample events at DAQ rates flooded
  the EventStore. Sample data lives in ChannelStore; lifecycle events
  (`ChannelStarted` / `ChannelClosed`) replace it.
- `StreamFrameIndex` event class — same flood problem at chunk rate.
  Live consumers read the file directly.

### Fixed

- `/channels/{id}` 500 error on pure-scalar channels.
- `Context.stream` and `channels.write` did not emit `ChannelStarted`
  (only the observer path did). All writer paths now emit it.
- `/live/{run_id}` Streams panel showed streams from all runs;
  subscription now scopes by `run_id`.

### Deferred to v0.3.0

- **Local shared-memory transport** (item 22) — PoC measured 2×
  latency over Flight loopback, not the 3–10× estimated. Revisit on
  symptoms (UI lag traced to transport; >10 kHz capture saturation).
- **Consumer SDK `litmus.live`** (item 20) — store APIs are
  available; higher-level consumer surface not yet designed.
- **Hardware video encoder formats** (item 23) — `mp4` / `wav` /
  `flac` handlers on top of the streaming-sink machinery.
- **Channels + files as test INPUTS** — current verbs are producer-only;
  a first-class input surface (`channels.read` / `files.read`) lands
  in v0.3.0.

## [0.1.3] - 2026-05-24

Documentation-heavy release. Full per-screen reference for the operator
UI plus a Diátaxis × topic reorg that lands every doc page in a
predictable `<quadrant>/<category>/<topic>.md` cell, with cross-quadrant
"See also" navigation between them.

### Added

- **16 per-screen reference pages** under `docs/reference/operator-ui/`
  — every NiceGUI page (Dashboard, Launch Test, Live monitor, Results
  list/detail, Metrics, Measurements, Events, Channels list/detail,
  System Designer, Stations, Products, Fixtures, Instruments, Tests)
  documented from the running source. Each carries a cropped
  testid-anchored screenshot.
- **Cropped UI screenshots** via new `scripts/regenerate-ui-screenshots.py`
  (Playwright + headless `litmus serve`) — manifest-driven, PNGs commit
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
  `reference/configuration.md`. Filename prefixes (`litmus-`,
  `catalog-`, `why-`) dropped — directory carries the namespace. Three
  former "why-" concept pages rewritten to read as concept references,
  not blog posts.
- **In-app docs viewer** now supports nested subdirectory pages: route
  changed to `{page:path}`, `_parse_section_outline` switched to
  `rglob`, sidebar groups render as accordion (current page's group
  auto-expands).
- **Operator-UI table cleanup**: dropped run UUIDs from operator-facing
  tables (operators identify runs by DUT serial + start time, not
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

- **Test race**: `_wait_for_run` in `tests/test_execution/test_class_step_containers.py`
  (and 6 e2e workflow tests) was polling for `RunStarted` then
  immediately reading partially-materialised steps. Now waits for the
  run to finalise (`ended_at IS NOT NULL`) before reading. Same shape
  as the `test_inputs_auto_projected_to_parquet` flake that was
  intermittent in pre-commit pytest.
- **System Designer auto-save**: `src/litmus/ui/pages/designer/page.py`
  was passing `fixture_data["points"]` to `save_fixture`, but
  `to_fixture_yaml()` returns key `"connections"`. Every auto-save
  raised `KeyError: 'points'` silently caught by the except clause.
- Docs viewer's `/docs/_assets/` path was being gobbled by the
  `{page:path}` catch-all and returning HTML instead of PNGs. Mounted
  as static files before the dynamic route.

## [0.1.2] - 2026-05-19

First installable PyPI release. Both 0.1.0 and 0.1.1 wheels shipped without
`litmus/data/` due to an over-broad `data` exclude pattern in
`pyproject.toml`, so the bundled pytest plugin failed to import on every
fresh install; those releases are yanked.

### Added

- `verify(...)` and `logger.measure(...)` accept a plain dict for `limit=`
  (coerced via `Limit.model_validate`). Tutorials and examples now use the
  dict form; `from litmus import Limit` stays available for the model object.
- `verify_requires_limit: bool | None` on `ProfileConfig` — set to `False`
  on a characterization profile to route `verify()` to record-only
  semantics when no limit resolves (instead of `MissingLimitError`).
- `litmus refs list` / `litmus refs show <topic>` — stream curated reference
  docs (`tiers`, `verify`, `mocks`, `profiles`) to stdout. CLAUDE.md
  templates now point agents at this CLI instead of baking absolute paths.

### Fixed

- Packaging: scoped the `data` exclude pattern in `pyproject.toml` to
  `/data` (top-level only) so `src/litmus/data/` ships in the wheel.
- Run outcome stamping is now retry-aware. A test that errors on attempt 1
  and passes on the `litmus_retry` retry stamps the RUN as `passed`
  (matching pytest-rerunfailures, STDF MIR.RTST_COD, and Jenkins flaky-
  test-handler conventions). The errored attempt's step row stays in
  the run for retest / flake analysis.

## [0.1.0] - 2026-04-15

Initial public release on PyPI as `litmus-test`.

### Added

- `@litmus_test` decorator for pytest-native hardware tests with vector
  expansion, limit checking, measurement recording, retries, and mock injection
- Station / fixture / product / sequence YAML configuration, loaded through a
  single store layer with Pydantic validation
- Instrument fixtures resolved from station config (no `conftest.py`
  boilerplate required)
- `--mock-instruments` mode for hardware-free development
- Parquet result storage with per-step instrument traceability
  (serial, cal due date, firmware)
- DuckDB-backed analytics layer over the Parquet silver/gold layout
- Operator UI (`litmus serve`) built on NiceGUI
- FastAPI HTTP API and MCP server, with parity between the two
- Capability matching (`litmus_match`) against an instrument catalog
- CLI: `litmus init`, `discover`, `station init`, `new-test`, `serve`, `runs`,
  `show`, `instrument list`, `mcp serve`, `setup`
- Optional extras for output formats (`stdf`, `hdf5`, `tdms`, `mdf4`),
  transports (`s3`, `gcs`, `azure`, `sftp`), and integrations (`pymeasure`,
  `ni`, `lxi`, `grafana`, `pdf`, `sbom`)

[Unreleased]: https://github.com/pragmatest-dev/litmus/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/pragmatest-dev/litmus/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/pragmatest-dev/litmus/releases/tag/v0.1.2
[0.1.1]: https://github.com/pragmatest-dev/litmus/releases/tag/v0.1.1
[0.1.0]: https://github.com/pragmatest-dev/litmus/releases/tag/v0.1.0
