# Test Limits

Limits define pass/fail criteria for measurements. Litmus checks every `spec.check(...)` and `logger.measure(...)` call against a configured `Limit` and records the outcome.

## Limit structure

```yaml
measurement_name:
  low: 3.135          # lower limit
  high: 3.465         # upper limit
  nominal: 3.3        # expected / target (for EQ/NE)
  units: V
  comparator: GELE    # default; see table below
  spec_ref: "..."     # optional traceability pointer
  ref: "..."          # delegate to a product-spec characteristic
```

At least one of `low`, `high`, `nominal`, or `ref` is required.

| Field        | Required | Description                                     |
|--------------|:--------:|-------------------------------------------------|
| `low`        | *        | Lower limit (* at least one of low/high/nominal/ref) |
| `high`       | *        | Upper limit                                     |
| `nominal`    |          | Expected value (EQ/NE comparators)              |
| `units`      |          | Unit of measure (for reporting)                 |
| `comparator` |          | Comparison type (default `GELE`)                |
| `spec_ref`   |          | Traceability reference                          |
| `ref`        |          | Delegate to `spec.<char_name>` (inherits limits, units, ref) |

## Where limits come from

When `logger.measure(name, value)` is called without `limit=`, resolution is:

1. **Explicit kwargs** — `logger.measure("v", val, low=..., high=..., units=...)`
2. **Method marker** — `@pytest.mark.litmus_limits(name={...})`
3. **Class marker** — inherited from the test class
4. **Sidecar YAML** — `limits:` block in `test_<module>.yaml` (supports condition-indexed bands, see below)
5. **Product spec** — `ref: "<name>"` delegation against the active `SpecContext`
6. **None** — characterization mode (unchecked, still recorded)

Method-level overrides class-level key-by-key; non-conflicting keys merge. Sidecar merges **under** markers — markers win on conflicts.

`spec.check(name, value)` bypasses this chain and reads directly from the active product spec.

## Marker form

```python
import pytest

@pytest.mark.litmus_limits(
    output_voltage={"low": 3.234, "high": 3.366, "units": "V"},
    efficiency={"ref": "efficiency"},               # delegate to product spec
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
  efficiency:      {ref: efficiency}           # product-spec delegation
  startup_current: {high: 50, comparator: LE, units: mA}
```

Sidecar is the preferred home for operator-edited limits — non-developers can tune without touching Python.

## Condition-indexed bands (`when:`)

When a single measurement needs different limits under different conditions, replace the flat limit dict with a **list of bands**. Each band carries a `when:` mapping; at measurement time the first band whose `when:` matches the active vector params applies. This mirrors the `conditions:` selector on product-spec characteristics.

```yaml
# test_power_board.yaml
limits:
  output_voltage:
    - when: {vin: 5.0, load: 0.1}
      low: 3.234
      high: 3.366
      units: V
    - when: {vin: 5.0, load: 0.8}
      low: 3.2
      high: 3.4
      units: V
    - when: {vin: 3.3}            # matches any load at vin=3.3
      low: 3.1
      high: 3.5
      units: V
    - when: {}                    # catch-all; place last
      low: 3.0
      high: 3.6
      units: V
```

Matching rules:

- Keys inside `when:` are **ANDed** — every key must match for the band to apply.
- Missing keys on a band mean "don't care" (the 3.3 V band above matches every `load`).
- Bands are scanned top-to-bottom; the **first** match wins.
- No match → `pytest.UsageError` at `logger.measure` / `verify` time (fail loud, not silent).
- An empty `when: {}` always matches; put it last as a default.

The match is performed against the current row's vector params, so the feature composes naturally with both native `@pytest.mark.parametrize` and the `vectors` fixture self-loop mode — every iteration re-resolves against the active row.

Bands can use any policy field a flat limit supports, including `tolerance_pct` against a product characteristic:

```yaml
limits:
  output_voltage:
    - when: {vin: 5.0}
      characteristic: output_voltage      # nominal from product spec
      tolerance_pct: 2.0                  # ±2% at vin=5.0
    - when: {vin: 3.3}
      characteristic: output_voltage
      tolerance_pct: 5.0                  # looser at vin=3.3
```

The flat scalar shape (`output_voltage: {low: 3.2, high: 3.4}`) still works — treat it as shorthand for a single band with `when: {}`.

## Explicit `limit=` kwarg

```python
from litmus.config.models import Limit

logger.measure("v", val, limit=Limit(low=3.2, high=3.4, units="V"))
```

## Callable limits

When a limit depends on other parameters:

```yaml
limits:
  output_voltage:
    callable: myproject.limits.output_voltage
```

```python
# myproject/limits.py
from litmus.config.models import Limit

def output_voltage(context) -> Limit:
    if context.get_param("temperature", 25) < 50:
        return Limit(low=3.1, high=3.5, units="V")
    return Limit(low=3.0, high=3.6, units="V")
```

Callables receive the Litmus `Context` (`get_param`, `params`, `last`, `observe`).

## Product-spec delegation (`ref:`)

`ref: "<char_name>"` looks up the characteristic on the active `SpecContext` and inherits its limits, units, and `spec_ref`. Works in markers and sidecar:

```python
# product selected via --product=power_board_v1 or litmus.yaml / profile
@pytest.mark.litmus_limits(output_voltage={"ref": "output_voltage"})
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

## Best practices

1. **Prefer `spec.check(name, v)`** when a product spec exists — limits, DUT pin, and `spec_ref` all flow automatically
2. **Use `ref:`** to delegate to product-spec characteristics instead of duplicating values
3. **Keep operator-tuned values in sidecar YAML** so non-developers can edit them
4. **Match names** — the first argument to `spec.check` / `logger.measure` must match the limit key
5. **Never hardcode** — no `assert 3.0 <= v <= 3.6` in test bodies; use markers, sidecar, or the spec
