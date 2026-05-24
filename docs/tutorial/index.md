# Tutorial: Engineer's First Project

Welcome to Litmus! This tutorial takes you from your first test to a production-ready test suite. Each step builds on the previous one, introducing one concept at a time.

## Learning Path

| Step | Goal | What You'll Learn |
|------|------|-------------------|
| [0. Quick Start](00-quickstart.md) | Smallest end-to-end loop | install, run, see results |
| [1. First Test](01-first-test.md) | Run something | pytest, project structure |
| [2. Running Without Hardware](02-mock-instruments.md) | Use mock mode | `--mock-instruments`, `mock_config` (station-config block of canned return values used when `--mock-instruments` is on) |
| [3. pytest-native tests](03-fixtures.md) | Log measurements | the core per-test [Litmus fixtures](../reference/pytest-native.md) — `context` is the ambient run/DUT/station/vector state (always available; carries sweep params when present, observations always), `verify` records the measurement row AND raises on FAIL, `logger.measure` records the row without raising (plus ~17 other public fixtures the plugin exposes — see [litmus-fixtures](../reference/litmus-fixtures.md)) |
| [4. Add Limits](04-limits.md) | Pass/fail criteria | [Limit](../reference/models.md), Measurement, [Outcome](../reference/models.md#enum-outcome) |
| [5. Test Configuration](05-configuration.md) | Configure in YAML | [sidecar](05-configuration.md) (YAML next to a test file carrying vectors and limits) |
| [6. Product Specifications](06-specifications.md) | Define [products](../concepts/configuration/products.md) | products/*.yaml, [characteristics](../concepts/configuration/capabilities.md) |
| [7. Real Instruments](07-real-instruments.md) | Talk to hardware | [stations/*.yaml](../concepts/configuration/stations.md), VISA, simulation |
| [8. Capability Matching](08-capabilities.md) | Find compatible [stations](../concepts/configuration/stations.md) | [direction flip, matching API](../concepts/configuration/capabilities.md) |
| [9. Production Ready](09-production.md) | Full [traceability](../how-to/traceability.md) | [fixtures](../concepts/configuration/fixtures.md), test classes, sidecar |
| [10. Live Monitoring](10-live-monitoring.md) | Monitor in real time | [sessions](../concepts/data/sessions.md), [events](../concepts/data/event-log.md), [channels](../concepts/data/three-stores.md) |

## Prerequisites

- Python 3.11 or later
- Basic familiarity with pytest
- Litmus installed (`uv sync` or `pip install -e .`)

## Time Commitment

Each step takes 10-15 minutes. You can complete the entire tutorial in about 2 hours, or work through it over several sessions.

## What You'll Build

By the end of this tutorial, you'll have:

1. A working test suite for a voltage converter
2. YAML-based configuration for limits and test parameters
3. Tests that run with real instruments OR in simulation mode
4. Automatic station matching based on required capabilities
5. Full traceability from requirements to results
6. Live monitoring of test sessions with event queries

## Quick Start

If you just want to see working code:

```bash
litmus init quick_start --starter && cd quick_start
pytest
```

Then come back here to understand how it works.

## Ready?

[Start with Step 1: First Test →](01-first-test.md)
