---
name: litmus-tests
description: Use whenever a user wants to test, measure, characterize, or log any hardware value with Litmus — from a single bench reading to a limit-checked production test. Starts simple (zero config) and grows only as needed (sidecar limits, mock instruments, guardbands, sweeps). The front door for building a Litmus test solution.
---

# Building a Litmus test

Litmus tests are plain `pytest` functions plus Litmus fixtures. Your job: turn the
user's request into the **smallest correct test**, then grow only as the request
demands. Never scaffold config the request doesn't need.

## 1. Pick the verb (the decision that matters)

Ask: **is this value a Measurement?** (the TestStand distinction)

| The value is… | verb | becomes |
|---|---|---|
| raw evidence / a reading you won't judge / "did X happen" / a capture | `observe(name, value)` | an output |
| a measurement you record but don't limit-check | `measure(name, value)` | a measurement |
| a measurement judged against a limit (a spec parameter) | `verify(name, value, limit=...)` | a judged measurement |
| samples over time (a waveform) | `stream(...)` | a channel → see `litmus-capture` |

`measure` and `verify` share a signature — start with `measure`, add a limit and
change one word to `verify` when a spec exists. If unsure whether it's judged, ASK.

**`observe` attaches raw data or context to a measurement** — a supporting reading
(a temperature), a captured waveform, an image, a "did X happen" flag. Reach for it
whenever you want to keep evidence alongside a result; it lands as an output on the
run. Want that evidence **live and continuous** — samples over time, not one captured
value? That's `stream(...)`, the live sibling of `observe`; it lands as a channel (see
`litmus-capture`). The **one** thing NOT to `observe` is an *input*: a sweep param (the
`vin: float` a test takes from `litmus_sweeps`) or an instrument setpoint is already
recorded as an input automatically, so `observe("vin", vin)` just duplicates it into
the outputs lane.

## 2. Write the smallest test — zero config

No station, no YAML:

```python
def test_rail_voltage(verify):
    verify("rail_voltage", 3.31, limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

Record-only (no limit needed):

```python
def test_rail_readout(observe):
    observe("rail_voltage", 3.31)
```

Scaffold a starting file with `litmus new-test <name>` (writes
`tests/test_<name>.py` with a `context, <roles>, verify` signature), or just write
it directly. `verify` with no resolvable limit raises — always supply one (inline,
or from a sidecar/spec below).

## 3. Where does the limit live? (right-size the config)

Climb only as far as the request needs — stop at the lowest rung that works:

| Rung | You write | You add |
|---|---|---|
| 0 | `verify("v", x, limit={...})` | nothing |
| 1 | `verify("v", x)` (limit in sidecar) | a `<test>.yaml` sidecar |
| 2 | `test(psu, dmm)` + `--mock-instruments` | a station (or `litmus init --tier bringup`) → `litmus-stations`, `litmus-mocks` |
| 3 | `verify("v", x)` (limit from spec) | a part spec + `characteristic:` in the sidecar → `litmus-parts` |
| 4 | `--test-profile` / `--test-phase` | profiles → `litmus-profiles` |

Sidecar `<test>.yaml` sits next to the test file (same stem), operator-editable:

```yaml
limits:
  rail_voltage: {low: 3.2, high: 3.4, unit: V}
```

## 4. Limits & guardbands (accuracy matters)

A **part-spec characteristic** is the documented datasheet spec. A **test limit** is
usually *tighter*, set with a guardband. Emit the guardband form — never a hardcoded
band that silently detaches from the spec:

```yaml
# test limit = the datasheet nominal, tightened by 5%
limits:
  rail_voltage: {characteristic: rail_voltage, guardband_pct: 5}
```

- direct `{low, high, unit}` — test-owned, unrelated to the spec.
- `{characteristic: X, guardband_pct: N}` (or `tolerance_pct` / `tolerance_abs`) —
  derived off the spec nominal, tighter → use this for "tighter than datasheet".
- reference the characteristic with no band → inherits the spec band 1:1.

Defining the characteristic itself is a `litmus-parts` job.

## 5. Sweeps

Outer is the default — one vector per pytest item:

```python
import pytest

@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_line_reg(verify, vin): ...
```

or a sidecar `sweeps: [{vin: [4.5, 5.0, 5.5]}]`. Inner looping (the `vectors`
fixture inside the body) is an optimization for expensive setup — teach outer first.

## 6. Run it, then validate

```bash
pytest                     # zero-config tests
pytest --mock-instruments  # with mock instruments (needs a station)
litmus validate            # check any YAML you emitted
```

Run the test, and if you emitted any sidecar / station / part YAML, `litmus
validate` it before declaring done — correct by construction.

## Best-practice defaults
- **Right-size:** smallest artifact for the request; no station/part YAML for a one-off.
- **Verb by the Measurement test;** `measure`→`verify` is a one-word upgrade.
- **Guardband off the spec;** never hardcode a band that should track the datasheet.
- **Outer sweeps** by default.

## Deeper
Read the docs:
```bash
litmus docs show concepts/overview/tiers
litmus docs show how-to/execution/writing-tests
litmus docs show how-to/execution/limits
litmus docs show how-to/execution/spec-driven-testing
litmus docs show how-to/execution/test-context
```
Sibling skills: `litmus-mocks`, `litmus-stations`, `litmus-parts`, `litmus-profiles`,
`litmus-sites`, `litmus-capture`, `litmus-analysis`, `litmus-debug`, `litmus-interactive`.
