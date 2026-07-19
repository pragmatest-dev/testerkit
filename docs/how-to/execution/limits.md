# Test Limits

Limits define pass/fail criteria for measurements. TesterKit checks every `verify(...)` and `measure(...)` call against a configured `Limit` and records the outcome.

## Limit structure

```yaml
measurement_name:
  low: 3.135          # lower limit
  high: 3.465         # upper limit
  nominal: 3.3        # expected / target (for EQ/NE)
  unit: V
  comparator: GELE    # default; see table below
  spec_ref: "..."          # optional traceability pointer
  characteristic: "..."    # delegate to a part-spec characteristic
```

A limit needs at least one policy field that tells `verify` what to check. The flat-scalar shape above (`low` / `high` / `nominal` / `characteristic`) is the common case; the [Condition-indexed bands](#condition-indexed-bands) section below covers the `bands:` shape. To set a window around a part-spec nominal, add `tolerance_pct` or `tolerance_abs` alongside a `characteristic:`.

| Field            | Required | Description                                     |
|------------------|:--------:|-------------------------------------------------|
| `low`            | *        | Lower limit                                     |
| `high`           | *        | Upper limit                                     |
| `nominal`        |          | Expected value (EQ/NE comparators)              |
| `unit`           |          | Unit of measure (for reporting)                 |
| `comparator`     |          | Comparison type (default `GELE`)                |
| `spec_ref`       |          | Traceability annotation (free-form string)      |
| `characteristic` |          | Delegate to `part.<char_name>` (inherits limits, unit) |

\* At least one policy field is required: `low`, `high`, `nominal`, `characteristic`, or `bands` (or `tolerance_pct` / `tolerance_abs` paired with a `characteristic`).

## Where limits come from

`verify` and `measure` look up the limit the same way. If you pass `limit=` explicitly, it's used as-is and nothing else is checked. Otherwise the lookup tries, in order, and the **first match wins**:

1. **Explicit `limit=`** — `verify("v", val, limit={"low": ..., "high": ..., "unit": "V"})` (dict literal or `Limit(...)`).
2. **Active limits for `name`** — merged from the marker / sidecar / profile cascade (precedence below).
3. **Part spec** — if nothing matched and a part is selected, an unmatched `name` falls back to a part-spec characteristic of the same name. For condition-indexed bands, declare `characteristic:` explicitly so sweep values forward correctly (see [Spec-driven testing](spec-driven-testing.md#condition-indexed-example-when-accuracy-varies-with-operating-point)).
4. **None** — characterization mode: `measure` records the value with `outcome = DONE`; `verify` raises `MissingLimitError`. To let `verify` record without a limit, set `verify_requires_limit: false` in the active profile.

**Cascade precedence** (weakest → strongest, last to set a key wins): inline class marker → inline method marker → sidecar file → sidecar class → sidecar per-test → profile chain. So a sidecar entry overrides an inline decorator, and a profile overrides both.

## Marker form

```python
import pytest

@pytest.mark.testerkit_limits(
    output_voltage={"low": 3.234, "high": 3.366, "unit": "V"},
    efficiency={"characteristic": "efficiency"},    # delegate to part spec
    startup_current={"high": 50, "comparator": "LE", "unit": "mA"},
)
def test_rails(context, measure, dmm):
    measure("output_voltage", dmm.measure_dc_voltage())
    measure("startup_current", measure_startup(...))
```

Class-level applies to every method; method-level overrides per-key:

```python
@pytest.mark.testerkit_limits(output_voltage={"low": 3.2, "high": 3.4})
class TestPowerBoard:
    @pytest.mark.testerkit_limits(output_voltage={"low": 3.25, "high": 3.35})  # tighter
    def test_precise(self, measure, dmm): ...

    def test_normal(self, measure, dmm): ...     # uses class-level
```

## Sidecar YAML form

```yaml
# tests/test_power_board.yaml
limits:
  output_voltage:  {low: 3.135, high: 3.465, unit: V}
  efficiency:      {characteristic: efficiency}   # part-spec delegation
  startup_current: {high: 50, comparator: LE, unit: mA}
```

The same `limits:` field works at class-branch scope
(`tests.<Cls>.limits:`) and per-test scope (`tests.<name>.limits:`
or nested `tests.<Cls>.tests.<method>.limits:`). Per-test overrides
class overrides file-level, key-by-key.

Sidecar is the preferred home for operator-edited limits — non-developers can tune without touching Python.

## Condition-indexed bands

When a single measurement needs different limits under different conditions, add a `bands:` list inside the limit dict. Each band carries a `when:` mapping plus the fields it overrides. The dict's top-level fields are **defaults** — bands inherit them and override per-row. At measurement time the first band whose `when:` matches the active conditions wins.

```yaml
# test_power_board.yaml
limits:
  output_voltage:
    unit: V                               # default for every band
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

The match is performed against the active row's values, so it works with both `@pytest.mark.parametrize` and TesterKit sweeps — every iteration re-checks against the current row.

The default cascade keeps repetition out of the YAML. Common fields (`unit`, `characteristic`) live once at the top; bands carry only what changes. Bands can use the same policy fields as a flat limit — `low` / `high` / `nominal`, or `tolerance_pct` against a part characteristic:

```yaml
limits:
  output_voltage:
    characteristic: output_voltage              # nominal from part spec — shared
    bands:
      - {when: {vin: 5.0}, tolerance_pct: 2.0}     # ±2% at vin=5.0
      - {when: {vin: 3.3}, tolerance_pct: 5.0}     # looser at vin=3.3
```

A limit without `bands:` is the flat scalar shape (`output_voltage: {low: 3.2, high: 3.4}`) — equivalent to a single catch-all that always applies.

## Explicit `limit=` kwarg

```python
measure("v", val, limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

Same shape works on `verify(name, value, limit={...})`. Need the model object for type-checking or as a shared constant? Import from the top-level package: `from testerkit import Limit`.

## Part-spec delegation (`characteristic:`)

`characteristic: "<char_name>"` looks up the characteristic on the active `PartContext` and inherits its limits and units. Works in markers and sidecar:

```python
# part selected via --part=power_board_v1 or testerkit.yaml / profile
@pytest.mark.testerkit_limits(output_voltage={"characteristic": "output_voltage"})
def test_rails(...): ...
```

Use this when the part YAML is the source of truth and tests are thin wrappers.

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
measure("thermal_resistance", measure_rtheta())   # recorded, unchecked
```

Values show up in the parquet output for post-hoc analysis.

### `MissingLimitError` — why `verify` won't fall through to "unchecked"

`verify` is judgment-bearing — calling it with no resolvable limit raises `MissingLimitError` rather than silently recording the value. The error names every source that was checked — `limit=` kwarg, sidecar / marker / profile cascade, and the active part spec — so the missing source is obvious.

If you genuinely want to record without judging, use `measure(name, value)` instead — it records the value with `outcome = DONE` and never raises on missing limits. The two methods divide cleanly: `verify` if a pass/fail decision belongs on the row, `measure` if not.

## Best practices

1. **Prefer `verify(name, v)`** when a part spec exists — limits, UUT pin, and `spec_ref` all flow automatically
2. **Use `characteristic:`** to delegate to part-spec characteristics instead of duplicating values
3. **Keep operator-tuned values in a sidecar `limits:` field** so non-developers can edit them
4. **Match names** — the first argument to `verify` / `measure` must match the limit key
5. **Never hardcode** — no `assert 3.0 <= v <= 3.6` in test bodies; use `limits` (sidecar / profile) or `@pytest.mark.testerkit_limits` (inline) or the part spec


## See also

**Related quadrants:**

- [Concepts → Execution](../../concepts/execution/index.md) — concepts entry point for this category
- [Reference](../../reference/index.md) — reference entry point for this category
- [Integration](../../integration/index.md) — integration entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
