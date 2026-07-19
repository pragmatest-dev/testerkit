---
name: testerkit-mocks
description: Use when a user wants to run TesterKit tests without real hardware attached — dev-machine smoke tests, CI, a bench that's tied up, or pinning what a specific instrument call returns for one test.
---

# Running TesterKit tests without hardware

Two independent mock layers stack on top of each other. Pick the right one —
they solve different problems.

| Layer | Turns on | Scope | Use it to… |
|---|---|---|---|
| Station mock | `--mock-instruments` + `mock_config:` in the station YAML | every instrument, every test | make the whole session bench-less and instant |
| Per-test mock | `testerkit_mocks` marker (inline / sidecar / profile) | one fixture attribute, one test | pin an exact return value one test needs |

## 1. Station-level mock (bench-less session)

Requires a station (see `testerkit-stations`) with `mock_config:` on each
instrument you want config-driven values for:

```yaml
# stations/bench_01.yaml
instruments:
  dmm:
    type: dmm
    driver: drivers.DMM
    resource: TCPIP::192.168.1.102::INSTR
    mock_config:
      measure_dc_voltage: 3.3
      measure_dc_current: 0.042
```

```bash
pytest --mock-instruments
```

Under `--mock-instruments` (or `mock: true` on one instrument, or
`testerkit.yaml: mock_instruments: true`), TesterKit builds each instrument as
`Mock(object, **mock_config)` — a generic mock built off `object`, **not**
off the real driver class. Two consequences to warn about every time:

- **`isinstance(dmm, MyDriverClass)` is `False`.** Test code or fixtures
  that branch on the driver's type will not recognize the mock.
- **A typo in a `mock_config:` key is silent.** `measure_dc_voltag: 3.3`
  (misspelled) does not raise — the real method name resolves to an
  unconfigured no-op that returns `None`, and the test only fails later
  when `verify()`/`observe()` chokes on a `None` value (or worse, doesn't).
  Read the value back after writing `mock_config:` and confirm the key
  matches the exact method name the driver exposes.

## 2. Per-test mock (`testerkit_mocks`)

Pin what one fixture's attribute returns for one test — inline:

```python
import pytest

@pytest.mark.testerkit_mocks([
    {"target": "dmm.measure_dc_voltage", "return_value": 3.31},
    {"target": "psu.measure_current",     "return_value": 0.005},
])
def test_voltreg(dmm, psu, verify):
    verify("vout", dmm.measure_dc_voltage())
```

or sidecar (same shape, same file that carries `limits:` — see
`testerkit-tests`):

```yaml
# test_voltreg.yaml
mocks:
  - target: dmm.measure_dc_voltage
    return_value: 3.31
```

`target` is always `"<fixture>.<attr>"` — the fixture name from the test's
signature, then the attribute to patch on it. Every other key forwards
verbatim to `unittest.mock.patch.object` (`return_value`, `side_effect`,
`wraps`, `spec`, `spec_set`, `autospec`, `new_callable`). This works whether
`dmm` resolved to a real driver or a station-level mock — `testerkit_mocks`
patches the object the fixture already produced.

A `target` naming a fixture that isn't in the test's signature warns and
is skipped — it does not fail the run. A `target` with no `.` raises at
load time.

## Cascade

`testerkit_mocks` entries merge inline → sidecar → profile, last-wins by
`target` — the same merge rule as `testerkit_limits` and every other TesterKit
marker field. See `testerkit-profiles` for the full cascade and how a
profile can swap mock values per test phase.

## When to reach for which

- **Bare `--mock-instruments`, no `testerkit_mocks`** — fastest path to a
  bench-less smoke test. Every `mock_config:` value is a static number;
  fine for "does this test collect and run," weak for asserting a
  specific measurement.
- **`testerkit_mocks` on top** — needed the moment a test asserts on a
  specific value (`verify("vout", 3.31, ...)`), or you want a value that
  differs between two tests sharing the same station.
- **Real hardware, no flags** — `mock_config:`/`testerkit_mocks` are inert;
  `dmm` resolves to the real driver.

## Run it

```bash
pytest --mock-instruments          # station mock, whole session
pytest -k test_voltreg              # exercise one testerkit_mocks test
testerkit validate                     # check any station/sidecar YAML you edited
```

## Best-practice defaults
- Reach for the station mock first — it's zero-code and covers collection.
- Add `testerkit_mocks` only when a test needs a specific pinned value.
- Never assume `isinstance` works on a mocked instrument — check by
  behavior (return value), not by type.
- Verify a `mock_config:` key against the driver's real method name before
  trusting a `--mock-instruments` run — a typo won't tell you it's wrong.

## Deeper
Read the docs:
```bash
testerkit docs show how-to/configuration/mock-mode
```
Sibling skills: `testerkit-tests` (verb choice, sidecar), `testerkit-stations`
(station YAML, driver wiring), `testerkit-profiles` (phase-varying mocks).
