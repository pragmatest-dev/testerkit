# Test Limits

Limits define pass/fail criteria for measurements. Litmus checks every `verify(...)` and `logger.measure(...)` call against a configured `Limit` and records the outcome.

## Limit structure

```yaml
measurement_name:
  low: 3.135          # lower limit
  high: 3.465         # upper limit
  nominal: 3.3        # expected / target (for EQ/NE)
  units: V
  comparator: GELE    # default; see table below
  spec_ref: "..."          # optional traceability pointer
  characteristic: "..."    # delegate to a product-spec characteristic
```

A limit needs at least one policy field that tells `verify` what to check. The flat-scalar shape above (`low` / `high` / `nominal` / `characteristic`) is the common case; the [Condition-indexed bands](#condition-indexed-bands) section below covers the `bands:` shape, and [Limits beyond min/max](#limits-beyond-minmax) covers tolerance, expression, lookup-table, and stepped forms.

| Field            | Required | Description                                     |
|------------------|:--------:|-------------------------------------------------|
| `low`            | *        | Lower limit (* at least one policy field: low / high / nominal / characteristic / bands / tolerance_pct / tolerance_abs / expr / lookup / steps / callable) |
| `high`           | *        | Upper limit                                     |
| `nominal`        |          | Expected value (EQ/NE comparators)              |
| `units`          |          | Unit of measure (for reporting)                 |
| `comparator`     |          | Comparison type (default `GELE`)                |
| `spec_ref`       |          | Traceability annotation (free-form string)      |
| `characteristic` |          | Delegate to `product.<char_name>` (inherits limits, units) |

## Where limits come from

Both `verify(name, value)` and `logger.measure(name, value)` go through the same resolver. When `limit=` is passed explicitly, that value short-circuits the rest — every other source is ignored. Otherwise the resolver checks, in this order, and the **first match wins**:

1. **Explicit `limit=`** — `verify("v", val, limit=Limit(low=..., high=..., units="V"))` or `logger.measure(...)` with the same kwarg. Short-circuits everything below.
2. **Active limits entry for `name`** — populated from the sidecar / marker / profile cascade (merged into one entry per measurement name at test setup; details below).
3. **Active product spec** — if the cascade has nothing and `verify` is in play, the resolver tries the active `ProductContext` for a characteristic named `name`. This works for unconditional characteristics; condition-indexed bands need the explicit `characteristic:` delegation in step 2 to forward sweep params correctly (see [Spec-driven testing](spec-driven-testing.md#condition-indexed-example--when-accuracy-varies-with-operating-point)).
4. **None** — characterization mode. `logger.measure` records the value with `outcome = DONE`. `verify` raises `MissingLimitError` — judgment-bearing calls don't silently fall through.

The cascade inside step 2 walks file → class → test → profile, with later entries overriding earlier ones key-by-key per measurement name:

1. Sidecar **file-level** field — `limits: {...}` at the YAML root
2. Sidecar **class branch** field — `tests.<Cls>.limits: {...}`
3. Sidecar **per-test** field — `tests.<name>.limits: {...}` (or nested `tests.<Cls>.tests.<method>.limits: {...}`)
4. Inline `@pytest.mark.litmus_limits(...)` on method or class
5. Profile chain — parent profile first, leaf last

`verify(name, value)` does NOT bypass this chain — it walks the same resolver, and adds the `MissingLimitError` behavior in step 4 if nothing produces a limit.

## Marker form

```python
import pytest

@pytest.mark.litmus_limits(
    output_voltage={"low": 3.234, "high": 3.366, "units": "V"},
    efficiency={"characteristic": "efficiency"},    # delegate to product spec
    startup_current={"high": 50, "comparator": "LE", "units": "mA"},
)
def test_rails(context, logger, dmm):
    logger.measure("output_voltage", dmm.measure_dc_voltage())
    logger.measure("startup_current", measure_startup(...))
```

Class-level applies to every method; method-level overrides per-key:

```python
@pytest.mark.litmus_limits(output_voltage={"low": 3.2, "high": 3.4})
class TestPowerBoard:
    @pytest.mark.litmus_limits(output_voltage={"low": 3.25, "high": 3.35})  # tighter
    def test_precise(self, logger, dmm): ...

    def test_normal(self, logger, dmm): ...     # uses class-level
```

## Sidecar YAML form

```yaml
# tests/test_power_board.yaml
limits:
  output_voltage:  {low: 3.135, high: 3.465, units: V}
  efficiency:      {characteristic: efficiency}   # product-spec delegation
  startup_current: {high: 50, comparator: LE, units: mA}
```

The same `limits:` field works at class-branch scope
(`tests.<Cls>.limits:`) and per-test scope (`tests.<name>.limits:`
or nested `tests.<Cls>.tests.<method>.limits:`). Per-test overrides
class overrides file-level, key-by-key.

Sidecar is the preferred home for operator-edited limits — non-developers can tune without touching Python.

## Condition-indexed bands

When a single measurement needs different limits under different conditions, add a `bands:` list inside the limit dict. Each band carries a `when:` mapping plus the fields it overrides. The dict's top-level fields are **defaults** — bands inherit them and override per-row. At measurement time the first band whose `when:` matches the active vector params wins.

```yaml
# test_power_board.yaml
limits:
  output_voltage:
    units: V                              # default for every band
    low: 3.0                              # catch-all (used when no band matches)
    high: 3.6
    bands:
      - {when: {vin: 5.0, load: 0.1}, low: 3.234, high: 3.366}
      - {when: {vin: 5.0, load: 0.8}, low: 3.2,   high: 3.4}
      - {when: {vin: 3.3},            low: 3.1,   high: 3.5}   # any load at vin=3.3
```

Matching rules:

- Keys inside `when:` are **ANDed** — every key must match for the band to apply.
- Missing keys on a band mean "don't care" (the 3.3 V band above matches every `load`).
- Bands are scanned top-to-bottom; the **first** match wins.
- Siblings to `bands:` are the catch-all by design — used when no band's `when:` matches. No `when: {}` entry needed.
- No catch-all + no band match: the parent has no policy fields, so the measurement records in characterization mode (`outcome=DONE`, no pass/fail). Provide siblings if you want strict behavior.

The match is performed against the current row's vector params, so the feature composes naturally with both native `@pytest.mark.parametrize` and Litmus sweeps — every iteration re-resolves against the active row.

The default cascade keeps repetition out of the YAML. Common fields (`units`, `characteristic`) live once at the top; bands carry only what changes. Bands can use any policy field a flat limit supports, including `tolerance_pct` against a product characteristic:

```yaml
limits:
  output_voltage:
    characteristic: output_voltage              # nominal from product spec — shared
    bands:
      - {when: {vin: 5.0}, tolerance_pct: 2.0}     # ±2% at vin=5.0
      - {when: {vin: 3.3}, tolerance_pct: 5.0}     # looser at vin=3.3
```

A limit without `bands:` is the flat scalar shape (`output_voltage: {low: 3.2, high: 3.4}`) — equivalent to a single catch-all that always applies.

## Explicit `limit=` kwarg

```python
from litmus.models.test_config import Limit

logger.measure("v", val, limit=Limit(low=3.2, high=3.4, units="V"))
```

## Product-spec delegation (`characteristic:`)

`characteristic: "<char_name>"` looks up the characteristic on the active `ProductContext` and inherits its limits and units. Works in markers and sidecar:

```python
# product selected via --product=power_board_v1 or litmus.yaml / profile
@pytest.mark.litmus_limits(output_voltage={"characteristic": "output_voltage"})
def test_rails(...): ...
```

Use this when the product YAML is the source of truth and tests are thin wrappers.

## Comparators

| Comparator       | Pass condition           |
|------------------|--------------------------|
| `GELE` (default) | `low ≤ value ≤ high`     |
| `GELT`           | `low ≤ value < high`     |
| `GTLE`           | `low < value ≤ high`     |
| `GTLT`           | `low < value < high`     |
| `GE`             | `value ≥ low`            |
| `GT`             | `value > low`            |
| `LE`             | `value ≤ high`           |
| `LT`             | `value < high`           |
| `EQ`             | `value == nominal`       |
| `NE`             | `value ≠ nominal`        |

## Characterization mode (no limits)

Omit all sources to record values without pass/fail:

```python
logger.measure("thermal_resistance", measure_rtheta())   # recorded, unchecked
```

Values show up in the parquet output for post-hoc analysis.

### `MissingLimitError` — why `verify` won't fall through to "unchecked"

`verify` is judgment-bearing — calling it with no resolvable limit raises `MissingLimitError` (importable from `litmus.execution.verify`) rather than silently recording the value. The error names every source the resolver checked — `limit=` kwarg, sidecar / marker / profile cascade, and the active product spec — so the missing source is obvious.

If you genuinely want to record without judging, use `logger.measure(name, value)` instead — it records the value with `outcome = DONE` and never raises on missing limits. The two methods divide cleanly: `verify` if a pass/fail decision belongs on the row, `logger.measure` if not.

## Best practices

1. **Prefer `verify(name, v)`** when a product spec exists — limits, DUT pin, and `spec_ref` all flow automatically
2. **Use `characteristic:`** to delegate to product-spec characteristics instead of duplicating values
3. **Keep operator-tuned values in a sidecar `limits:` field** so non-developers can edit them
4. **Match names** — the first argument to `verify` / `logger.measure` must match the limit key
5. **Never hardcode** — no `assert 3.0 <= v <= 3.6` in test bodies; use `limits` (sidecar / profile) or `@pytest.mark.litmus_limits` (inline) or the product spec
