# Reference

Authoritative listing of Litmus's public surface, organized by what you're holding when you reach for it. Each entry links to the page that exhaustively documents one boundary.

## Writing pytest tests

pytest is the foundation; the bundled plugin layers fixtures, markers, and YAML on top. Read in order — each page assumes the previous.

- [pytest-native](pytest-native.md) — how a Litmus test uses pytest's own collection / fixtures / markers / `conftest.py` / CLI flags. Nothing Litmus-specific. The baseline every other page builds on.
- [Litmus fixtures](litmus-fixtures.md) — all 20 fixtures the plugin contributes on top of pytest's, with signatures, scopes, and per-fixture examples.
- [Litmus markers](litmus-markers.md) — the seven `@pytest.mark.litmus_*` decorators the plugin registers and their 1:1 sidecar equivalents.
- [Test configuration](configuration.md) — `litmus.yaml`, station YAML, fixture YAML, sidecar YAML, profile YAML — the schemas the fixtures and markers read from.

## Authoring instrument catalog YAML

The catalog is shared across projects (one entry per `make.model`), so its schema gets its own pages.

- [Catalog schema](catalog-schema.md) — every field in a `catalog/<vendor>/<model>.yaml` entry, the rules, the "what goes WHERE" decision tree.
- [Catalog cookbook](catalog-cookbook.md) — worked recipes for the recurring datasheet shapes (accuracy bands, dual-unit values, shared controls, conditional attributes, etc.).

## Submitting results from outside pytest

For LabVIEW, TestStand, scripts, the operator UI — anywhere the test isn't a pytest function.

- [`LitmusClient`](client.md) — Python client that submits test runs (no pytest required). Suits LabVIEW / TestStand bridges.
- [`connect()`](connect.md) — interactive instrument access for scripts, notebooks, the operator UI. Returns a `StationConnection` with the full event-log / channel-store / instrument-pool surface.
- [HTTP API](api.md) — REST endpoints exposed by `litmus serve`. Same shapes as the MCP tools.

## Data shapes the system writes

If you're reading the parquet, the event log, or any export — these pages describe exactly what's in them.

- [Pydantic models](models.md) — every public model + ERD of how they reference each other.
- [Event types](event-types.md) — every typed event payload the runtime emits.
- [Parquet schema](parquet-schema.md) — every column in the run parquet, the `record_type` discriminator, how retries land.
- [Output formats](outputs.md) — what `litmus show -f <fmt>` and `litmus export` produce for HTML / PDF / JSON / CSV.

## Reading results back

For analytics from Python — yield, Cpk, pareto, trends — without writing raw DuckDB.

- [Query API](query-api.md) — `RunsQuery`, `StepsQuery`, `MeasurementsQuery`. The public read path the UI and HTTP API both use.

## Operator UI (`litmus serve`)

Per-screen reference for the browser surface. For orientation, see the [Tour of the Operator UI](../how-to/overview/operator-ui-tour.md).

- [Dashboard](operator-ui/dashboard.md) — `/`
- [Launch Test](operator-ui/launch.md) — `/launch`
- [Live monitor](operator-ui/live.md) — `/live/{run_id}`
- [Results — list](operator-ui/results/list.md) — `/results`
- [Results — detail](operator-ui/results/detail.md) — `/results/{run_id}`
- [Metrics](operator-ui/metrics.md) — `/metrics`
- [Measurements](operator-ui/measurements.md) — `/explore`
- [Events](operator-ui/events.md) — `/events`
- [Channels — list](operator-ui/channels/list.md) — `/channels`
- [Channels — detail](operator-ui/channels/detail.md) — `/channels/{channel}`
- [System Designer](operator-ui/designer.md) — `/designer`
- [Stations](operator-ui/stations.md) — `/stations`
- [Products](operator-ui/products.md) — `/products`
- [Fixtures](operator-ui/fixtures.md) — `/fixtures`
- [Instruments](operator-ui/instruments.md) — `/instruments`
- [Tests](operator-ui/tests.md) — `/tests`

## Command line

- [CLI reference](cli.md) — every `litmus <command>` and its flags.

## AI integration

- [Skills](skills.md) — the workflows, sub-agent templates, and slash commands that ship with Litmus for AI-assisted authoring.
