# Concepts

Why Litmus is built the way it is. Concepts are grouped by what you're trying to understand. Each group has its own index; jump to the page you care about, or read top-to-bottom for the full mental model.

## Overview

[Overview index →](overview/index.md). The framework's mental model from above — what Litmus is, what it isn't, and why pytest sits underneath.

- [Architecture](overview/architecture.md) — system-level view of products, stations, fixtures, and runs
- [Platform vs framework](overview/platform-vs-framework.md) — what the platform owns vs what the runner owns
- [pytest](overview/pytest.md) — why a hardware-test platform rides on pytest instead of its own runner
- [AI integration](overview/ai-integration.md) — what the MCP surface buys you and where it draws the line

## Configuration

[Configuration index →](configuration/index.md). The DUT-to-instrument model — the YAML entities you author once and re-use across runs.

- [Products](configuration/products.md) — what you're testing
- [Stations](configuration/stations.md) — where you test
- [Capabilities](configuration/capabilities.md) — what instruments can do, how matching pairs them with product characteristics
- [Fixtures](configuration/fixtures.md) — pin-to-instrument mapping

## Execution

[Execution index →](execution/index.md). How a test run unfolds — the step model, the outcome ladder, what each step records.

- [Step hierarchy](execution/step-hierarchy.md) — how test classes, methods, and vectors nest
- [Step manifest](execution/step-manifest.md) — what each step records
- [Outcomes](execution/outcomes.md) — passed / failed / errored / skipped / done / terminated / aborted severity ladder

## Data

[Data index →](data/index.md). Where the run data lives and how the platform stays consistent across processes.

- [Event log](data/event-log.md) — the durable record of every run
- [Event sourcing](data/event-sourcing.md) — append-only event log as the source of truth
- [Sessions](data/sessions.md) — connect-to-disconnect observation windows for instrument use
- [Three stores](data/three-stores.md) — EventStore, ChannelStore, ParquetBackend; on-disk layout, data_dir resolution, schema-evolution contract
- [Flight streaming](data/flight-streaming.md) — cross-process data access via Arrow Flight
