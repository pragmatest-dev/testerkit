# Changelog

All notable changes to Litmus are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0 note: the public API is unstable. Breaking changes are possible in any
0.x release and will be called out in this changelog.

## [Unreleased]

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
