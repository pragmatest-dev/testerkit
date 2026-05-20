# Per-test mock overrides

Mock values bind through the `litmus_mocks` marker (inline, sidecar, or
profile). Each mock entry targets `"<fixture>.<attr>"` and forwards all
other keys to `unittest.mock.patch.object`. The `--mock-instruments`
flag swaps real drivers for `MagicMock(spec=DriverClass)` at session
start; `litmus_mocks` then pins specific attribute returns per test.

## Inline (decorator)

```python
import pytest

@pytest.mark.litmus_mocks([
    {"target": "dmm.measure_dc_voltage", "return_value": 3.31},
    {"target": "psu.measure_current",     "return_value": 0.005},
])
def test_voltreg(dmm, psu, verify):
    psu.set_voltage(5.0); psu.enable_output()
    verify("vout", dmm.measure_dc_voltage())
    verify("iq",   psu.measure_current())
```

The marker takes a **list of dicts**; each dict is one mock entry.
Stack the marker at function, class, or module level — same merge
order as `litmus_limits`.

## Sidecar (YAML)

```yaml
# <test_file>.yaml
mocks:
  - target: dmm.measure_dc_voltage
    return_value: 3.31
  - target: psu.measure_current
    return_value: 0.005
```

`mocks:` is a top-level list under the entry (file scope) or under
`tests.<TestClass>.<test_name>` (per-test). Same merge order as
`limits:` — class scope applies to every method on the class.

## Entry schema

| Key | Type | Required | Notes |
|-----|------|----------|-------|
| `target` | `"<fixture>.<attr>"` | **yes** | Validated; missing `.` raises |
| `return_value` | any | no | Static return for the patched callable |
| `side_effect` | callable / iterable / exception | no | Per `unittest.mock.patch.object` |
| `wraps`, `spec`, `spec_set`, `autospec`, `new_callable` | per stdlib | no | Forwarded verbatim |

`MockEntry` uses `extra="allow"` — any keyword `patch.object` accepts
works.

## Resolution

`litmus_mocks` entries merge across the marker stack (inline + sidecar
+ profile) in the same shape as `litmus_limits`. The merged list is
applied as `patch.object` calls around each test invocation. Patches
unwind on test exit so cross-test bleed is not a concern.

## When to use what

- **`--mock-instruments` flag alone** — gives every instrument a
  `MagicMock(spec=DriverClass)`. Bench-less smoke tests pass, but
  every `measure_*` returns a `MagicMock` (truthy but not numeric);
  `verify` will raise on the cast.
- **`litmus_mocks` for return values** — pins what each method
  returns so `verify(name, float(dmm.measure_dc_voltage()))` lands a
  real number.
- **Real drivers without `--mock-instruments`** — Tier 2+ catalogs
  resolve real driver classes from the bench; `litmus_mocks` is
  unused.

Pre-Tier-2 projects often skip `litmus_mocks` entirely and pin returns
inside the `conftest.py` `MagicMock` fixture itself — fine for a few
tests, but `litmus_mocks` lets the per-test mock value live next to
the test that needs it.
