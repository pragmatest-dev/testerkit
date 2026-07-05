# `verify`

`verify` is the runner-neutral judgment-bearing measurement primitive.
One call does three things:

1. Records the value as a `measurement` row in the run's parquet log
2. Resolves the active limit and compares the value against it
3. Raises `AssertionError` (subclass `LimitFailure`) on FAIL

Use [`observe`](observe.md) for record-only measurements
(characterization / setup readouts where no limit applies).

## Signature

```python
verify(
    name: str,
    value: float | int | None,
    limit: Limit | dict | None = None,        # inline limit; optional
    *,
    characteristic: str | None = None,        # derive the limit from a part characteristic
    namespace: str | None = None,             # group the measurement
    unit: str | None = None,                  # engineering unit
) -> Measurement
```

**Record-only sibling: `measure`.** Same signature as `verify`, but it **never
judges and never raises on a missing limit** — it stamps one measurement row
with `Outcome.DONE`. Use it to capture a value without pass/fail (characterization,
diagnostics, logged context): `measure("vin_actual", 12.03)`. (`observe` is
different — it writes to the *output lane*, not a measurement row; see
[`observe`](observe.md).)

Available as a pytest fixture (`def test_foo(verify, ...): verify(...)`).
The OpenHTF adapter and `LitmusClient` surface the same callable
through their native shapes.

## Limit shape

`limit=` accepts either a `Limit` Pydantic model or a plain dict —
verify coerces dicts via `Limit.model_validate(...)`:

```python
verify("v_rail", v, limit={"low": 3.135, "high": 3.465, "unit": "V"})
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `unit` | `str` | **yes** | e.g. `"V"`, `"A"`, `"ohm"` |
| `low` | `float \| None` | no | Lower bound (inclusive by default) |
| `high` | `float \| None` | no | Upper bound (inclusive by default) |
| `nominal` | `float \| None` | no | Target value; required for `EQ` / `NE` |
| `comparator` | `str` enum | no | `GELE` (default), `GELT`, `GTLE`, `GTLT`, `EQ`, `NE`, `GE`, `GT`, `LE`, `LT` |
| `characteristic_id` | `str \| None` | no | For part-spec traceability |
| `spec_ref` | `str \| None` | no | Human-readable spec citation (e.g. `"Table 4.2 @ 25°C"`) |

`extra="forbid"` — unknown keys raise validation errors.

For the model object (useful as a shared module constant or for IDE
type-checking):

```python
from litmus import Limit
V_RAIL = Limit(low=3.135, high=3.465, unit="V")
```

## Limit resolution chain

`verify` walks the chain top-down and uses the first hit:

1. `verify(..., limit=...)` passed by the caller
2. `@pytest.mark.litmus_limits(name={...})` on the test / class / module
3. `<test_file>.yaml` sidecar `limits: {<name>: {...}}` entry
4. Active profile's `limits:` block
5. Active `PartContext` characteristic matching `name` (or `characteristic:` override)
6. `None` → `MissingLimitError` (unless the active profile sets `verify_requires_limit: false`)

## Sidecar `limits:` schema

```yaml
# <test_file>.yaml — same shape as the dict form, plus part-spec helpers
limits:
  v_rail:
    low: 3.135
    high: 3.465
    unit: V
  output_voltage:
    characteristic: rail_3v3      # delegate to PartContext['rail_3v3']
    tolerance_pct: 5.0            # ±5 % around the spec's nominal
```

Three resolution shapes live under one key:

- **Absolute bounds:** `{low, high, unit}` (and optionally `nominal`, `comparator`)
- **Part delegate:** `{characteristic: <id>}` — copies the characteristic's `low/high/unit/nominal/spec_ref`
- **Tolerance band:** `{characteristic: <id>, tolerance_pct: N}` — derives `low`/`high` from the spec's `nominal` ± `N %`

## Outcomes

The recorded `Outcome` is:

- `PASSED` — value within bounds per the active comparator
- `FAILED` — value outside bounds; `verify` raises `LimitFailure(AssertionError)`
- `ERRORED` — value is `None` (couldn't measure)
- `DONE` — `verify_requires_limit: false` profile + no resolvable limit → record-only fallthrough
