# Tutorial: Engineer's First Project

Welcome to Litmus! This tutorial takes you from your first test to a production-ready test suite. Each step builds on the previous one, introducing concepts only when you need them.

## Learning Path

| Step | Goal | What You'll Learn |
|------|------|-------------------|
| [1. First Test](01-first-test.md) | Run something | pytest, conftest.py, basic test |
| [2. Add Measurement](02-measurement.md) | Measure a voltage | MockDMM, @litmus_test decorator |
| [3. Add Limits](03-limits.md) | Pass/fail based on limits | Measurement, Limit, Outcome |
| [4. YAML Configuration](04-configuration.md) | Configure without code | config.yaml, spec.yaml |
| [5. Real Instruments](05-real-instruments.md) | Talk to hardware | stations/*.yaml, VISA, simulate=True |
| [6. Capability Matching](06-capabilities.md) | Find compatible stations | capabilities, direction flip |
| [7. Production Ready](07-production.md) | Full traceability | fixtures, sequences, results |

## Prerequisites

- Python 3.11 or later
- Basic familiarity with pytest
- Litmus installed (`uv sync` or `pip install -e .`)

## Time Commitment

Each step takes 10-15 minutes. You can complete the entire tutorial in about 90 minutes, or work through it over several sessions.

## What You'll Build

By the end of this tutorial, you'll have:

1. A working test suite for a voltage converter
2. YAML-based configuration for limits and test parameters
3. Tests that run with real instruments OR in simulation mode
4. Automatic station matching based on required capabilities
5. Full traceability from requirements to results

## Quick Start

If you just want to see working code:

```bash
cd demo && python run_demo.py
```

Then come back here to understand how it works.

## Ready?

[Start with Step 1: First Test →](01-first-test.md)
