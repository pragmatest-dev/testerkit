# Concepts

Why Litmus is built the way it is. Read top-to-bottom for the framework's mental model, or jump to the group that matches what you're trying to understand.

## Foundations

- [Why pytest](why-pytest.md) — why a hardware-test framework rides on pytest instead of its own runner
- [Platform architecture](platform-architecture.md) — Litmus as platform, not test framework
- [Architecture](architecture.md) — system-level view of products, stations, fixtures, and runs

## The DUT-to-instrument model

- [Products](products.md) — what you're testing
- [Stations](stations.md) — where you test
- [Capabilities](capabilities.md) — what an instrument can do, how capabilities/signals/conditions compose, and how matching pairs them with product characteristics
- [Fixtures](fixtures.md) — pin-to-instrument mapping

## Execution & outcomes

- [Step hierarchy](step-hierarchy.md) — how test classes, methods, and vectors nest
- [Step manifest](step-manifest.md) — what each step records
- [Outcomes](outcomes.md) — passed / failed / errored / skipped / done / terminated / aborted severity ladder
- [Sessions](sessions.md) — connect-to-disconnect observation windows for instrument use

## Data architecture

- [Why event sourcing](why-event-sourcing.md) — append-only event log as the source of truth
- [Event log](event-log.md) — the durable record of every run
- [Three stores](three-stores.md) — EventStore, ChannelStore, ParquetBackend; on-disk layout, data_dir resolution, schema-evolution contract
- [Flight streaming](flight-streaming.md) — cross-process data access via Arrow Flight

## AI integration

- [Why AI integration](why-ai-integration.md) — what AI-assisted authoring buys you, why this is supportive rather than magic, and how to adopt at your own pace
