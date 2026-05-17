# Step 4: Add Limits

**Goal:** Decide pass/fail for a measurement.

In step 3 your tests called `verify(..., limit=Limit(...))` or `logger.measure(..., limit=Limit(...))` to record a measurement. The pass/fail decision happens because a **[limit](../reference/models.md)** is present. This step is about the `Limit` shape and the two ways to attach a limit to a test from code: inline on the call, or via a Litmus marker on the test function. Both pass the limit through the same resolution chain.

Step 5 will move limits out of code and into a YAML file next to the test — keep that destination in mind, but don't reach for YAML yet.

## The `Limit` shape

`Limit` is re-exported from the top-level `litmus` package (defined in `src/litmus/models/test_config.py`):

```python
from litmus import Limit

limit = Limit(
    low=3.135,      # Minimum acceptable value
    high=3.465,     # Maximum acceptable value
    nominal=3.3,    # Expected value (optional)
    units="V",      # Unit of measure
)
```

## How a measurement is checked

The `logger.measure(...)` call records a [`Measurement`](../reference/models.md) row with the value, units, and limit. `verify(...)` does the same plus raises `AssertionError` on FAIL. Either way, the row carries an `Outcome`:

| Outcome | String value | Meaning |
|---------|--------------|---------|
| `Outcome.PASSED` | `"passed"` | Value within limits |
| `Outcome.FAILED` | `"failed"` | Value outside limits |
| `Outcome.SKIPPED` | `"skipped"` | Test was skipped |
| `Outcome.ERRORED` | `"errored"` | Test encountered an error |
| `Outcome.ABORTED` | `"aborted"` | Run aborted by operator |
| `Outcome.TERMINATED` | `"terminated"` | Run terminated (keyboard interrupt, signal) |
| `Outcome.DONE` | `"done"` | Container outcome — work finished, no measurements |

Source: `Outcome` in `src/litmus/data/models.py`. Container outcomes roll up via the ladder `skipped < done < passed < failed < errored < terminated < aborted` — the worst child wins (`skipped` and `done` rank below `passed` so a parent with one skipped child and one passing child still resolves to `passed`).

## Inline limit on the call

The simplest form: pass `limit=` directly to `verify` or as `low=`/`high=`/`units=` kwargs on `logger.measure`. This is what you already saw in step 3 — recapped here for completeness:

```python
from litmus.models.test_config import Limit

def test_output_voltage(dmm, verify):
    verify(
        "output_voltage",
        dmm.measure_dc_voltage(),
        limit=Limit(low=3.135, high=3.465, units="V"),
    )
```

Or via `logger.measure`:

```python
def test_output_voltage(dmm, logger):
    logger.measure(
        "output_voltage",
        dmm.measure_dc_voltage(),
        low=3.135, high=3.465, units="V",
    )
```

Inline limits are fine for one-off tests. They clutter the test body when limits get long or vary per test.

## Limit via marker

The `litmus_limits` marker pulls the limit dict out of the body and pins it at the top of the test (`src/litmus/pytest_plugin/markers.py`):

```python
import pytest

@pytest.mark.litmus_limits(
    output_voltage={"low": 3.135, "high": 3.465, "units": "V"},
)
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

The marker accepts one keyword per measurement name. `verify("output_voltage", ...)` resolves the limit from the marker without you passing `limit=` explicitly. You can apply `@pytest.mark.litmus_limits` at function, class, or module level — class scope applies to every method on the class.

## Comparators

By default, limits use `GELE` (greater-or-equal to low, less-or-equal to high): `low <= value <= high`. Other comparators are available when the test needs a different shape:

```python
from litmus.models.enums import Comparator
from litmus.models.test_config import Limit

# Upper limit only
limit = Limit(high=1.0, comparator=Comparator.LE)  # value <= 1.0

# Lower limit only
limit = Limit(low=0.0, comparator=Comparator.GE)   # value >= 0.0

# Must equal nominal
limit = Limit(nominal=5.0, comparator=Comparator.EQ)
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

## Characterization mode (no limit)

During development you may want to record a value without deciding pass/fail. Drop the limit and `logger.measure` records the row with `measurement_outcome` left NULL (unchecked):

```python
def test_voltage(dmm, logger):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

(Units are derived from the active limit when one is present; in characterization mode the row is recorded without limit fields.)

`verify` requires a limit (it's the whole point of `verify`); use `logger.measure` for characterization.

## What's missing — and what step 5 fixes

Inline limits and markers live in the test code. That means a non-developer can't change them, condition-dependent limits get awkward, and limits can't be reused across multiple test files. Step 5 introduces the **[sidecar YAML](05-configuration.md)** — a file next to the test that carries limits (and sweeps, mocks, retries, prompts) without changing the test code.

For [condition-indexed bands](../how-to/limits.md#condition-indexed-bands) (different bands at different temperatures or loads) jump to [Test limits](../how-to/limits.md#condition-indexed-bands) when you need it.

## What you learned

- The `Limit` model — `low`, `high`, `nominal`, `units`, `comparator`
- Inline limits via `verify(..., limit=...)` or `logger.measure(..., limit=Limit(...))`
- The `litmus_limits` marker for class/function-level limit binding
- The `Outcome` ladder and what each value means
- The `Comparator` enum for non-`GELE` checks

## Next Step

Move the limits out of code and into a YAML file next to your test.

[Step 5: Test Configuration →](05-configuration.md)
