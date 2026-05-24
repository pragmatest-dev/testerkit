# Step 4: Add Limits

**Goal:** Decide pass/fail for a measurement.

In step 3 your tests called `verify(name, value, limit=...)`. The pass/fail decision happens because a **limit** is attached. This step covers the limit shape and the two ways to attach a limit from code: inline on the call, or via the `litmus_limits` marker on the test function. Both feed the same resolution chain.

Step 5 moves limits out of code into a YAML file next to the test — keep that destination in mind, but don't reach for YAML yet.

## The limit shape

A limit is a plain dict — same shape whether it lives inline, on a marker, or in YAML:

```python
limit = {
    "low": 3.135,    # Minimum acceptable value
    "high": 3.465,   # Maximum acceptable value
    "nominal": 3.3,  # Expected value (optional)
    "units": "V",    # Unit of measure
}
```

Both `verify(name, value, limit=...)` and `logger.measure(name, value, limit=...)` accept this dict directly. Internally it's validated against the `Limit` Pydantic model in [`litmus.models.test_config`](../reference/models.md#model-limit). If you'd rather construct the model explicitly — for IDE type-checking or for a shared constant — `Limit` is re-exported from the top-level package:

```python
from litmus import Limit

V_RAIL = Limit(low=3.135, high=3.465, units="V")
```

The dict form is the canonical idiom in tutorials and examples; reach for `Limit(...)` when you want the model object.

## How a measurement is checked

`logger.measure(...)` records a [`Measurement`](../reference/models.md#model-measurement) row with the value, units, and limit. `verify(...)` does the same plus raises `AssertionError` on FAIL. Either way, the row carries an `Outcome`:

| Outcome | String value | Meaning |
|---------|--------------|---------|
| `Outcome.PASSED` | `"passed"` | Value within limits |
| `Outcome.FAILED` | `"failed"` | Value outside limits |
| `Outcome.SKIPPED` | `"skipped"` | Test was skipped |
| `Outcome.ERRORED` | `"errored"` | Test encountered an error |
| `Outcome.ABORTED` | `"aborted"` | Run aborted by operator |
| `Outcome.TERMINATED` | `"terminated"` | Run terminated (keyboard interrupt, signal) |
| `Outcome.DONE` | `"done"` | Recorded without a limit, or container with no measurements |

Container outcomes roll up via the ladder `skipped < done < passed < failed < errored < terminated < aborted` — the worst child wins (`skipped` and `done` rank below `passed` so a parent with one skipped child and one passing child still resolves to `passed`).

## Inline limit on the call

The simplest form — pass `limit=` directly to `verify` or `logger.measure`:

```python
def test_output_voltage(dmm, verify):
    verify(
        "output_voltage",
        dmm.measure_dc_voltage(),
        limit={"low": 3.135, "high": 3.465, "units": "V"},
    )
```

Inline limits are fine for one-off tests. They clutter the test body when limits get long or vary per test.

## Limit via marker

`litmus_limits` pulls the limit out of the body and pins it at the top of the test:

```python
import pytest

@pytest.mark.litmus_limits(
    output_voltage={"low": 3.135, "high": 3.465, "units": "V"},
)
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

The marker accepts one keyword per measurement name. `verify("output_voltage", ...)` resolves the limit from the marker without an explicit `limit=`. You can apply `@pytest.mark.litmus_limits` at function, class, or module level — class scope applies to every method on the class.

## Comparators

By default, limits use `GELE` (greater-or-equal to low, less-or-equal to high): `low <= value <= high`. Other comparators are available when the test needs a different shape:

```python
# Upper limit only
limit = {"high": 1.0, "units": "A", "comparator": "LE"}     # value <= 1.0

# Lower limit only
limit = {"low": 0.0, "units": "V", "comparator": "GE"}      # value >= 0.0

# Must equal nominal
limit = {"nominal": 5.0, "units": "V", "comparator": "EQ"}
```

Full list:

| Comparator | Pass condition |
|------------|----------------|
| `GELE` | `low <= value <= high` (default) |
| `GELT` | `low <= value < high` |
| `GTLE` | `low < value <= high` |
| `GTLT` | `low < value < high` |
| `EQ` | `value == nominal` |
| `NE` | `value != nominal` |
| `GE` | `value >= low` |
| `GT` | `value > low` |
| `LE` | `value <= high` |
| `LT` | `value < high` |

## Recording without judging

`logger.measure` records a value without comparing it to a limit — pass no `limit=` and the row carries `Outcome.DONE`:

```python
def test_voltage(dmm, logger):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

`verify` is judgment-bearing: calling it with no limit (no inline `limit=`, no marker, no sidecar, no product spec) raises `MissingLimitError`. For a wide characterization sweep where you want the same `verify()` test code to record values without judging, set `verify_requires_limit: false` on a [profile](../how-to/execution/profiles.md) — `verify` then falls back to `logger.measure` semantics for that session.

## What's missing — and what step 5 fixes

Inline limits and markers live in the test code. That means a non-developer can't change them, condition-dependent limits get awkward, and limits can't be reused across multiple test files. Step 5 introduces the **[sidecar YAML](05-configuration.md)** — a file next to the test that carries limits (and sweeps, mocks, retries, prompts) without changing the test code.

For [condition-indexed bands](../how-to/execution/limits.md#condition-indexed-bands) (different bands at different temperatures or loads) jump to [Test limits](../how-to/execution/limits.md#condition-indexed-bands) when you need it.

## What you learned

- The limit dict — `low`, `high`, `nominal`, `units`, `comparator`
- Inline limits via `verify(..., limit={...})` or `logger.measure(..., limit={...})`
- The `litmus_limits` marker for class/function-level limit binding
- The `Outcome` ladder and what each value means
- The `Comparator` enum for non-`GELE` checks
- Recording without judging via `logger.measure(no limit)` or a record-only profile

## Continue

Move the limits out of code and into a YAML file next to your test.

← [Step 3: pytest-native tests](03-fixtures.md)  |  [Step 5: Test Configuration →](05-configuration.md)
