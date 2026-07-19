# Reference

Authoritative listing of TesterKit's public surface, organized by the same category axis as [concepts](../concepts/) and [how-to](../how-to/). Each entry links to the page that exhaustively documents one boundary.

## Overview

The foundational pages — pytest mechanics every TesterKit test uses, plus the inventory of AI / MCP skills the platform ships.

- [pytest-native](overview/pytest-native.md) — how a TesterKit test uses pytest's own collection / fixtures / markers / `conftest.py` / CLI flags. Nothing TesterKit-specific. The baseline every other page builds on.
- [Skills](overview/skills.md) — the workflows, sub-agent templates, slash commands, and MCP prompts that ship with TesterKit for AI-assisted authoring.

## Configuration

The YAML schemas every entity validates against, plus the catalog (its own thing because the catalog is shared across projects).

- [Configuration](configuration.md) — `testerkit.yaml`, station YAML, fixture YAML, part YAML, sidecar YAML, profile YAML — all schemas the fixtures and markers read from (generated from the Pydantic models).
- [Catalog → schema](catalog/schema.md) — every field in a `catalog/<vendor>/<model>.yaml` entry, the rules, the "what goes WHERE" decision tree.
- [Catalog → cookbook](catalog/cookbook.md) — worked recipes for the recurring datasheet shapes (accuracy bands, dual-unit values, shared controls, conditional attributes, etc.).

## pytest plugin

The fixtures and markers the bundled pytest plugin contributes on top of stock pytest.

- [Fixtures](pytest/fixtures.md) — all the fixtures the plugin contributes on top of pytest's, with signatures, scopes, and per-fixture examples.
- [Markers](pytest/markers.md) — the seven `@pytest.mark.testerkit_*` decorators the plugin registers and their 1:1 sidecar equivalents.

## Data

The shapes the system writes. If you're reading parquet, the event log, or any export — these pages describe exactly what's in them.

- [Models](data/models.md) — every public Pydantic model + ERD of how they reference each other (generated).
- [Event types](data/event-types.md) — every typed event payload the runtime emits (generated).
- [Parquet schema](data/parquet-schema.md) — every column in the run parquet, the `record_type` discriminator, how retries land.
- [Output formats](data/outputs.md) — what `testerkit show -f <fmt>` and `testerkit export` produce for HTML / PDF / JSON / CSV / STDF / HDF5 / TDMS / MDF4.
- [Query API](data/query-api.md) — `RunsQuery`, `StepsQuery`, `MeasurementsQuery`. The public read path the UI and HTTP API both use (generated).

## Runtime

The interactive and programmatic surfaces — for LabVIEW, TestStand, scripts, dashboards, AI agents.

- [`TesterKitClient`](runtime/client.md) — Python client that submits test runs (no pytest required). Suits LabVIEW / TestStand bridges.
- [`connect()`](runtime/connect.md) — interactive instrument access for scripts, notebooks, the operator UI. Returns a `StationConnection` with the full event-log / channel-store / instrument-pool surface.
- [HTTP & MCP API](runtime/api.md) — REST endpoints exposed by `testerkit serve`, plus the MCP tools (generated). Same shapes either way.

## Operator UI (`testerkit serve`)

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
- [Files](operator-ui/files.md) — `/files`
- [System Designer](operator-ui/designer.md) — `/designer`
- [Stations](operator-ui/stations.md) — `/stations`
- [Parts](operator-ui/parts.md) — `/parts`
- [Fixtures](operator-ui/fixtures.md) — `/fixtures`
- [Instruments](operator-ui/instruments.md) — `/instruments`
- [Tests](operator-ui/tests.md) — `/tests`

## Command line

- [CLI reference](cli.md) — every `testerkit <command>` and its flags (generated).
