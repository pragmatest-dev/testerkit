# Tutorial: Engineer's First Project

Welcome to Litmus! This tutorial takes you from your first test to a production-ready test suite. Each step builds on the previous one, introducing one concept at a time.

## Learning Path

| Step | Goal | What You'll Learn |
|------|------|-------------------|
| [1. First Test](01-first-test.md) | Run something | pytest, project structure |
| [2. Running Without Hardware](02-mock-instruments.md) | Use mock mode | `--mock-instruments`, `mock_config` |
| [3. The @litmus_test Decorator](03-decorator.md) | Log measurements | @litmus_test, vector, return values |
| [4. Add Limits](04-limits.md) | Pass/fail criteria | Limit, Measurement, Outcome |
| [5. Test Configuration](05-configuration.md) | Configure in YAML | sequences, vectors, limits |
| [6. Product Specifications](06-specifications.md) | Define products | products/*/spec.yaml, characteristics |
| [7. Real Instruments](07-real-instruments.md) | Talk to hardware | stations/*.yaml, VISA, simulation |
| [8. Capability Matching](08-capabilities.md) | Find compatible stations | direction flip, matching API |
| [9. Production Ready](09-production.md) | Full traceability | fixtures, sequences, pins |

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

## Quick Start

If you just want to see working code:

```bash
cd demo && python run_demo.py
```

Then come back here to understand how it works.

## Ready?

[Start with Step 1: First Test →](01-first-test.md)
