# Stage 3 — Limits as a pytest marker

Same drivers, same `verify`, same log. The only change from stage 2:
the limit is a marker on the test function, not a `Limit(...)`
instance inside the body.

## Diff from stage 2

- Deleted `from litmus.config.test_config import Limit` — no longer needed in the body
- Deleted the module-level `V_RAIL = Limit(...)` constant
- Added `@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "units": "V"})` on each test

## Run it

```bash
cd examples/03-inline-limits
uv run pytest -v
```

## Why this shape

`litmus_limits` is declarative. The limit is configuration, not
imperative code. That matters for two reasons:

1. **The marker composes.** Stack `@pytest.mark.litmus_sweeps` and
   `@pytest.mark.litmus_limits` — pytest handles both the same way.
2. **Configuration has a migration path.** Markers can move from
   decorator → sidecar YAML → profile YAML without touching the test
   function. The next stage shows that move.

## The gap this stage leaves

Limits are still in Python source. Ops teams who want to tighten
production limits ("keep dev's ±5 %, run prod at ±2 %") need a code
change + PR review + deploy. Stage 4 moves limits out of the `.py`
file into a sibling YAML sidecar so they can tune without editing
code.
