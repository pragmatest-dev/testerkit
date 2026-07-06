# Writing a Litmus test

A Litmus test is a plain `pytest` function. Litmus adds bare fixtures for the verbs
(`verify`, `measure`, `observe`, `stream`) and a config cascade (inline marker <
sidecar YAML < profile, last-wins) so limits, sweeps, and mocks move out of the test
body as a project grows. Nothing below requires a station, a part, or a profile until the request needs
one — see the ladder near the bottom.

## Smallest real test

```python
def test_rail_within_spec(verify) -> None:
    verify("v_rail", 3.31, limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

`pytest` collects and runs this with zero config. `verify` records the value as a
measurement row and judges it against the inline `limit=`. Full verb signature and
limit shape: `litmus refs show verify`.

## Growing it: sidecar YAML, then a sweep + instruments

Move the limit out of the body into a `<test_file>.yaml` sidecar (same stem):

```python
# test_rail.py
def test_rail_within_spec(verify) -> None:
    verify("v_rail", 3.31)          # no inline limit — resolved from the sidecar
```

```yaml
# test_rail.yaml
limits:
  v_rail: {low: 3.2, high: 3.4, unit: V}
```

`SidecarConfig` (`src/litmus/models/test_config.py`) validates this file —
`extra="forbid"`, so a typo'd key fails at load, not at run time. Add a sweep and
instruments the same way:

```python
import pytest

@pytest.mark.litmus_sweeps([{"vin": [3.3, 4.5, 5.5]}])
@pytest.mark.litmus_limits(v_rail={"low": 3.2, "high": 3.4, "unit": "V"})
def test_rail_holds_across_input(verify, psu, dmm, vin: float) -> None:
    psu.set_voltage(vin)
    psu.enable_output()
    verify("v_rail", dmm.measure_dc_voltage())
```

`psu` / `dmm` are not built in — they come from an active station's `instruments:`
map (or a bringup `conftest.py`; see Instruments below). `sweeps:` works as a sidecar
key too, in place of the inline marker.

## Steps & vectors — what parametrize actually produces

Every pytest item collected for a test function is a **step** (`(step_path,
vector_index)` identifies one executed step instance), carrying its own `inputs` — for
a parametrized test, that variant's parameter dict. `litmus_sweeps` / sidecar
`sweeps:` becomes one `metafunc.parametrize` call per axis-group (a single-key dict is
one axis; a multi-key dict zips paired axes, all lists the same length, enforced by
`SweepEntry` at YAML load). Three `vin` values means three steps, `vector_index`
0/1/2, each with its own `MeasurementRecorded` row. A step does **not** need a vector
at all — `test_rail_within_spec` has one step, `vector_index=0`, no sweep: vectors are
optional condition points, not a required execution unit. The `vectors` fixture is a
separate in-body pattern for looping an inner axis inside one pytest item — reach for
it only to amortize expensive setup or collapse a sweep into one analytics row. Full
hierarchy: `docs/concepts/execution/step-hierarchy.md`.

## Config cascade — sidecar OVERRIDES inline; last-wins on profile

Sidecar YAML, inline `@pytest.mark.litmus_*` decorators, and `profiles/<name>.yaml`
share one vocabulary (`limits`, `sweeps`, `mocks`, `characteristics`, `connections`,
`retry`, `prompts`). Merge order, least to most specific:

```
inline @pytest.mark.litmus_* decorators → sidecar (file → class → per-test)
  → selected profile chain (parent first, child last) → CLI flags
```

Later value wins on overlap (per measurement name, per mock target, etc.) — a sidecar
entry for a name the inline marker also sets **overrides** the inline value. Use an
inline marker for a limit/sweep that's a fact about the code (rare); use the sidecar
for values an operator tunes without touching Python (the default landing spot once a
value leaves the body); use a profile for a value that varies by **recurring lab
condition** (validation vs. production vs. characterization), selected by
`--test-phase=<facet>` or `--test-profile=<name>`.

`SidecarConfig` shape (a profile's root uses the same shape):

```yaml
# <test_file>.yaml
limits:
  v_rail: {low: 3.2, high: 3.4, unit: V}
tests:                       # mirrors pytest node-ids; optional nesting
  TestRails:                 # class branch — applies to every method
    sweeps:
      - {vin: [4.5, 5.0, 5.5]}
    tests:
      test_rail:              # nested leaf — most specific sidecar scope
        limits:
          v_rail: {low: 3.25, high: 3.35}
```

Deeper: `litmus refs show profiles` (facet selection, `extends:` chains,
station_type/fixture binding), `litmus refs show mocks` (the `mocks:` entry shape).

## Choosing the verb

`verify` vs `measure` vs `observe` vs `configure`/`stream` is a routing decision, not a
test-writing one — read `litmus refs show routing` first. One-line summary: judged
spec parameter → `verify`; measurement you'll never limit-check → `measure`; not a
measurement at all (report text, artifact, waveform) → `observe`; a stimulus you set →
imperative driver call, or `configure` only for a runtime-computed setpoint.

## Right-size the test — do the smallest thing

Don't add YAML the request doesn't need. Every rung below is a working test:

| Rung | You write | You need |
|---|---|---|
| 0 | `verify("v", x, limit={...})` / `observe("v", x)` | nothing |
| 1 | `verify("v", x)` (limit in sidecar) | a `<test>.yaml` sidecar |
| 2 | `test(psu, dmm)` + `--mock-instruments` | a station (or bringup `conftest.py`) |
| 3 | `verify("v", x)` (limit from part spec) | a part spec + `characteristic:` in the sidecar |
| 4 | `--test-profile` / `--test-phase` | profiles |

Full ladder: `litmus refs show tiers`. Bringup vs. production is a phase distinction,
not a different API: bringup tests lean on `measure`/`observe` (no limit needed yet);
production tests use `verify` with a limit from a sidecar, part spec, or profile.

## Instruments (mock first)

```python
# conftest.py — bringup tier, no station YAML
import pytest
from drivers import PSU               # user driver code — PyVISA, PyMeasure, vendor lib
from litmus import Mock

@pytest.fixture(scope="session")
def psu(mock_instruments) -> PSU:
    if mock_instruments:
        return Mock(PSU, measure_voltage=5.0, measure_current=0.042)
    return PSU(resource="TCPIP::192.168.1.101::INSTR")
# dmm follows the same shape, from drivers.DMM
```

`litmus.Mock(cls, **values)` returns an instance that passes `isinstance(x, cls)`;
every method is a no-op except the kwargs you name. `mock_instruments` resolves from
`--mock-instruments`/`--no-mock-instruments` > `LITMUS_MOCK_INSTRUMENTS` env var >
`litmus.yaml: mock_instruments:` > `false`. Litmus ships no instrument drivers.

## Running and viewing results

```bash
pytest                          # real hardware (or conftest mocks if mock_instruments defaults true)
pytest --mock-instruments       # swap real drivers for mocks at session start
pytest --uut-serial=SN0042      # stamp the DUT identity on the run
litmus runs                     # list recent runs
litmus show <run_id>            # show one run's steps/measurements in the terminal
litmus show <run_id> -f html    # generate a report
```

## Multi-site (skip unless the fixture has 2+ sites)

A `FixtureConfig` with 2+ `sites:` is `is_multi_site` — one bench, multiple UUT
positions tested in parallel, each a **site** (`site_index` position, `site_name` if
named). Test code never mentions a site or a channel; the fixture's per-site
`connections:` does the routing. Full model:
`docs/how-to/execution/multi-uut-testing.md`. Working example: `examples/12-parallel-sites`.
