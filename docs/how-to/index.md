# How-To Guides

Task recipes for specific jobs. Ordered by the workflow a test engineer actually walks: write a test, run it without hardware, attach real instruments, then move to production.

If you're new to Litmus, work through the [Tutorial](../tutorial/) first — these guides assume you can already run a simple test.

## Operator UI

- [Tour of the Operator UI](operator-ui-tour.md) — what each of the 13 sidebar entries does, with cross-links to per-screen reference
- [Find flaky tests](find-flaky-tests.md) — use Metrics → Retest and Results detail to spot intermittents
- [Compare two runs](compare-runs.md) — diff known-good vs failing with two tabs + a DuckDB query

## Write tests

- [Writing tests](writing-tests.md) — pytest classes, sidecar YAML, the `verify` pattern
- [Test limits](limits.md) — limit shapes, condition-indexed bands, comparator semantics
- [Test vectors & sweeps](vector-expansion.md) — sidecar `sweeps:`, `@parametrize`, the `vectors` fixture
- [Spec-driven testing](spec-driven-testing.md) — derive limits from the product YAML

## Run without hardware

- [Mock mode](mock-mode.md) — `--mock-instruments`, station `mock_config`, per-test mocks

## Add hardware

- [Configuring stations](configuring-stations.md) — station YAML, instruments, environments
- [Custom instrument drivers](custom-drivers.md) — bring your own driver (PyVISA / PyMeasure / vendor)
- [Context architecture](context-architecture.md) — what the ambient `context` fixture knows and where it comes from

## Run in production

- [Profiles — named config sets](profiles.md) — select which tests run and how
- [Managing sessions](managing-sessions.md) — connect/disconnect lifecycle for instrument usage
- [Multi-DUT testing](multi-dut-testing.md) — subprocess-per-slot, shared instruments
- [Measurement traceability](traceability.md) — ATML / IEEE 1671 (the industry test-data interchange standard Litmus aligns with) metadata captured automatically

## Query results

- [Querying historical events](querying-events.md) — MCP tool, HTTP API, Python
- [Querying channel data](querying-channels.md) — time-series data plane

## AI-assisted authoring

- [MCP integration](mcp-integration.md) — register the MCP server with Claude Code, Cursor, Copilot, Cline, Claude Desktop
- [Datasheet → tests with Claude Code](datasheet-to-test.md) — end-to-end walkthrough of the `datasheet-to-test` workflow

## Integrations

- [Grafana dashboards](grafana-dashboards.md) — pre-built dashboards for results, events, channels
