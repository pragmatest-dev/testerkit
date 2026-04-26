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

Limits flow through `litmus_limits` markers. When
`logger.measure(name, value)` is called without `limit=`, resolution
walks the full marker merge cascade (least → most specific):

1. **Explicit kwargs** — `logger.measure("v", val, low=..., high=..., units=...)`
2. **Sidecar file-level entry** — `config: [- litmus_limits: {...}]`
3. **Sidecar class branch entry** — `tests.<Cls>.config:`
4. **Sidecar per-test entry** — `tests.<name>.config:` (or nested `tests.<Cls>.tests.<method>.config:`)
5. **Inline `@pytest.mark.litmus_limits(...)`** on method / class
6. **Profile chain markers** — parent profile first, child last
7. **Product spec** — `ref: "<name>"` delegation against the active `SpecContext`
8. **None** — characterization mode (unchecked, still recorded)

Later stages override earlier ones key-by-key. Same-marker-same-key in
the same scope: later-declared wins.

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
config:
  - litmus_limits:
      output_voltage:  {low: 3.135, high: 3.465, units: V}
      efficiency:      {ref: efficiency}           # product-spec delegation
      startup_current: {high: 50, comparator: LE, units: mA}
```

The same `litmus_limits` entry works at class-branch scope
(`tests.<Cls>.config:`) and per-test scope (`tests.<name>.config:`
or nested `tests.<Cls>.tests.<method>.config:`). Per-test overrides
class overrides file-level, key-by-key.

Sidecar is the preferred home for operator-edited limits — non-developers can tune without touching Python.

## Condition-indexed bands

When a single measurement needs different limits under different conditions, add a `bands:` list inside the limit dict. Each band carries a `when:` mapping plus the fields it overrides. The dict's top-level fields are **defaults** — bands inherit them and override per-row. At measurement time the first band whose `when:` matches the active vector params wins.

```yaml
# test_power_board.yaml
config:
  - litmus_limits:
      output_voltage:
        units: V                              # default for every band
        bands:
          - {when: {vin: 5.0, load: 0.1}, low: 3.234, high: 3.366}
          - {when: {vin: 5.0, load: 0.8}, low: 3.2,   high: 3.4}
          - {when: {vin: 3.3},            low: 3.1,   high: 3.5}   # any load at vin=3.3
          - {when: {},                    low: 3.0,   high: 3.6}   # catch-all; last
```

Matching rules:

- Keys inside `when:` are **ANDed** — every key must match for the band to apply.
- Missing keys on a band mean "don't care" (the 3.3 V band above matches every `load`).
- Bands are scanned top-to-bottom; the **first** match wins.
- No match → `pytest.UsageError` at `logger.measure` / `verify` time (fail loud, not silent).
- An empty `when: {}` always matches; put it last as a default.

The match is performed against the current row's vector params, so the feature composes naturally with both native `@pytest.mark.parametrize` and `litmus_sweeps` — every iteration re-resolves against the active row.

The default cascade keeps repetition out of the YAML. Common fields (`units`, `characteristic`, `ref`) live once at the top; bands carry only what changes. Bands can use any policy field a flat limit supports, including `tolerance_pct` against a product characteristic:

```yaml
config:
  - litmus_limits:
      output_voltage:
        ref: output_voltage                   # nominal from product spec — shared
        bands:
          - {when: {vin: 5.0}, tolerance_pct: 2.0}     # ±2% at vin=5.0
          - {when: {vin: 3.3}, tolerance_pct: 5.0}     # looser at vin=3.3
```

A limit without `bands:` is the flat scalar shape (`output_voltage: {low: 3.2, high: 3.4}`) — equivalent to one band with `when: {}`.

## Explicit `limit=` kwarg

```python
from litmus.config.models import Limit

logger.measure("v", val, limit=Limit(low=3.2, high=3.4, units="V"))
```

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
3. **Keep operator-tuned values in a sidecar `litmus_limits` marker** so non-developers can edit them
4. **Match names** — the first argument to `spec.check` / `logger.measure` must match the limit key
5. **Never hardcode** — no `assert 3.0 <= v <= 3.6` in test bodies; use `litmus_limits` markers (inline, sidecar, or profile) or the product spec
