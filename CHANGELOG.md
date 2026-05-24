# Changelog

All notable changes to Litmus are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0 note: the public API is unstable. Breaking changes are possible in any
0.x release and will be called out in this changelog.

## [Unreleased]

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

[Unreleased]: https://github.com/pragmatest-dev/litmus/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/pragmatest-dev/litmus/releases/tag/v0.1.2
[0.1.1]: https://github.com/pragmatest-dev/litmus/releases/tag/v0.1.1
[0.1.0]: https://github.com/pragmatest-dev/litmus/releases/tag/v0.1.0
