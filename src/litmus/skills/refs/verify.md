# `verify`

`verify` is the runner-neutral judgment-bearing measurement primitive.
One call does three things:

1. Records the value as a `measurement` row in the run's parquet log
2. Resolves the active limit and compares the value against it
3. Raises `AssertionError` (subclass `LimitFailure`) on FAIL

Use `logger.measure` for record-only measurements (characterization /
setup readouts where no limit applies).

## Signature

```python
verify(
    name: str,
    value: float | int | None,
    limit: Limit | dict | None = None,        # inline limit; optional
    characteristic: str | None = None,        # ProductContext key
) -> Measurement
```

Available as a pytest fixture (`def test_foo(verify, ...): verify(...)`).
The OpenHTF adapter and `LitmusClient` surface the same callable
through their native shapes.

## Limit shape

`limit=` accepts either a `Limit` Pydantic model or a plain dict —
verify coerces dicts via `Limit.model_validate(...)`:

```python
verify("v_rail", v, limit={"low": 3.135, "high": 3.465, "units": "V"})
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `units` | `str` | **yes** | e.g. `"V"`, `"A"`, `"ohm"` |
| `low` | `float \| None` | no | Lower bound (inclusive by default) |
| `high` | `float \| None` | no | Upper bound (inclusive by default) |
| `nominal` | `float \| None` | no | Target value; required for `EQ` / `NE` |
| `comparator` | `str` enum | no | `GELE` (default), `GELT`, `GTLE`, `GTLT`, `EQ`, `NE`, `GE`, `GT`, `LE`, `LT` |
| `characteristic_id` | `str \| None` | no | For product-spec traceability |
| `spec_ref` | `str \| None` | no | Human-readable spec citation (e.g. `"Table 4.2 @ 25°C"`) |

`extra="forbid"` — unknown keys raise validation errors.

For the model object (useful as a shared module constant or for IDE
type-checking):

```python
from litmus import Limit
V_RAIL = Limit(low=3.135, high=3.465, units="V")
```

## Limit resolution chain

`verify` walks the chain top-down and uses the first hit:

1. `verify(..., limit=...)` passed by the caller
2. `@pytest.mark.litmus_limits(name={...})` on the test / class / module
3. `<test_file>.yaml` sidecar `limits: {<name>: {...}}` entry
4. Active profile's `limits:` block
5. Active `ProductContext` characteristic matching `name` (or `characteristic:` override)
6. `None` → `MissingLimitError` (unless the active profile sets `verify_requires_limit: false`)

## Sidecar `limits:` schema

```yaml
# <test_file>.yaml — same shape as the dict form, plus product-spec helpers
limits:
  v_rail:
    low: 3.135
    high: 3.465
    units: V
  output_voltage:
    characteristic: rail_3v3      # delegate to ProductContext['rail_3v3']
    tolerance_pct: 5.0            # ±5 % around the spec's nominal
```

Three resolution shapes live under one key:

- **Absolute bounds:** `{low, high, units}` (and optionally `nominal`, `comparator`)
- **Product delegate:** `{characteristic: <id>}` — copies the characteristic's `low/high/units/nominal/spec_ref`
- **Tolerance band:** `{characteristic: <id>, tolerance_pct: N}` — derives `low`/`high` from the spec's `nominal` ± `N %`

## Outcomes

The recorded `Outcome` is:

- `PASSED` — value within bounds per the active comparator
- `FAILED` — value outside bounds; `verify` raises `LimitFailure(AssertionError)`
- `ERRORED` — value is `None` (couldn't measure)
- `DONE` — `verify_requires_limit: false` profile + no resolvable limit → record-only fallthrough
