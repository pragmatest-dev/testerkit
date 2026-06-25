# How-To Guides

Task recipes for specific jobs. Each group below mirrors the same axis used by [concepts](../concepts/) and [reference](../reference/): the recipe you want is at `how-to/<category>/<task>.md`, the explanation is at `concepts/<category>/<topic>.md`, the lookup is at `reference/<category>/<item>.md`.

If you're new to Litmus, work through the [tutorial](../tutorial/) first — these guides assume you can already run a simple test.

## Overview

Orientation map of the platform's surfaces.

- [Tour of the Operator UI](overview/operator-ui-tour.md) — what each sidebar entry does, with cross-links to per-screen reference
- [AI-assisted test development via MCP](overview/mcp-integration.md) — register the MCP server with Claude Code, Cursor, Copilot, Cline, Claude Desktop

## Configuration

Get hardware on the bench and wired into a station.

- [Configuring stations](configuration/configuring-stations.md) — station YAML, instruments, environments
- [Custom instrument drivers](configuration/custom-drivers.md) — bring your own driver (PyVISA / PyMeasure / vendor)
- [Mock mode](configuration/mock-mode.md) — `--mock-instruments`, station `mock_config`, per-test mocks

## Execution

Author and run tests.

- [Writing tests](execution/writing-tests.md) — pytest classes, sidecar YAML, the `verify` pattern
- [Test limits](execution/limits.md) — limit shapes, condition-indexed bands, comparator semantics
- [Test vectors & sweeps](execution/vector-expansion.md) — sidecar `sweeps:`, `@parametrize`, the `vectors` fixture
- [Spec-driven testing](execution/spec-driven-testing.md) — derive limits from the part YAML
- [Read and write the test context](execution/test-context.md) — what the `context` fixture knows and how to use it from inside a test
- [Profiles — named config sets](execution/profiles.md) — select which tests run and how
- [Managing sessions](execution/managing-sessions.md) — connect/disconnect lifecycle for instrument usage
- [Multi-UUT testing](execution/multi-uut-testing.md) — run multiple UUTs in parallel, with shared instruments
- [Measurement traceability](execution/traceability.md) — UUT / part / pin / instrument identity captured automatically
- [Operator prompts](execution/operator-prompts.md) — pause a test for operator input with the `litmus_prompts` marker and `prompt` fixture

## Data

Capture, query, debug, export, and dashboard the data a run produces.

- [Querying historical events](data/querying-events.md) — MCP tool, HTTP API, Python
- [Querying channel data](data/querying-channels.md) — time-series instrument data
- [Choosing a channel verb](data/choosing-a-channel-verb.md) — query / latest / live / window, picked by intent
- [Capture a waveform](data/capture-waveform.md) — `observe` + `verify` for scope traces and derived scalars
- [Stream a live channel](data/stream-live-channel.md) — `channels.stream` from interactive code; live UI updates
- [Capture an artifact](data/capture-an-artifact.md) — attach an image / capture file / record to a measurement
- [Find flaky tests](data/find-flaky-tests.md) — use Metrics → Retest and Results detail to spot intermittents
- [Compare two runs](data/compare-runs.md) — diff known-good vs failing with two tabs + a DuckDB query
- [Export results](data/export-results.md) — `litmus show -f` for reports (HTML/PDF/JSON/CSV) and `litmus export -f` for interchange (STDF/HDF5/TDMS/MDF4)
- [Query runs and metrics via MCP](data/mcp-query-runs.md) — `litmus_runs` / `litmus_steps` / `litmus_metrics` recipes
- [Debug failures via MCP](data/mcp-debug-failures.md) — chained investigative workflow when a run fails
- [Grafana dashboards](data/grafana-dashboards.md) — pre-built dashboards for results, events, channels
- [Benchmark your machine](data/benchmarking.md) — `litmus benchmark` measures per-store throughput

## Catalog

AI-assisted authoring against the capability catalog.

- [Datasheet → tests with Claude Code](catalog/datasheet-to-test.md) — end-to-end walkthrough of the `datasheet-to-test` workflow
