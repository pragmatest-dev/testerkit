# Your first Litmus test (from scratch, no datasheet)

The **start-simple** path: get a passing test with zero config, then adopt advanced pieces only as you
need them. Every rung below is a working test — climb only as far as your project requires. (For a
datasheet-driven part + test, use `datasheet-to-test.md` instead — that's the config-first road.)

## Rung 0 — a test with nothing (no YAML, no station, no part spec)

The Litmus pytest plugin always provides the verbs as bare fixtures. Nothing else is needed to begin.

```python
def test_rail_readout(observe) -> None:
    observe("rail_voltage", 3.28)                 # record a reading; never judges, never raises

def test_rail_in_spec(verify) -> None:
    verify("rail_voltage", 3.28, limit={"low": 3.0, "high": 3.6, "unit": "V"})   # judge vs a limit
```

Run `pytest`. Both pass with **no config at all**. The verbs:

- `observe(name, value)` — record a reading (scalar / waveform / blob / URI) to the output lane.
- `measure(name, value)` — record a **measurement row** without judging it (characterization).
- `verify(name, value, limit=...)` — record **and judge** a measurement. The limit is required
  (inline here, or from a sidecar / part spec below); `verify` with no resolvable limit raises.
- `stream(name, sample)` — append a sample to a channel.

## Rung 1 — move limits (and mocks) to a sidecar

When values belong to the operator rather than the code, put them in a `<test_file>.yaml` sidecar next
to the test:

```yaml
# test_rail.yaml
tests:
  test_rail_in_spec:
    limits:
      rail_voltage: {low: 3.0, high: 3.6, unit: V}
```

Now the test drops the inline limit — `verify("rail_voltage", 3.28)` resolves it from the sidecar.
(Sidecar keys, all optional: `limits:`, `sweeps:` (a list), `mocks:` (a list). `litmus refs show verify`.)

## Rung 2 — add instruments (mock first)

`psu` / `dmm` fixtures are **not** built in — they come from an active **station**'s `instruments:` map.
The fastest way to get them is to scaffold: `litmus init --tier bringup` writes a `conftest.py` with
`MagicMock`-shaped `psu`/`dmm` and a starter station.

```python
def test_output(verify, psu, dmm) -> None:
    psu.set_voltage(3.3)
    psu.enable_output()
    verify("output_voltage", float(dmm.measure_dc_voltage()),
           limit={"low": 3.0, "high": 3.6, "unit": "V"})
```

`pytest --mock-instruments` swaps **mock** drivers in for the station's declared roles — it does **not**
invent `psu`/`dmm`; a station (or the bringup `conftest.py`) must declare them.

## Rung 3 — a part spec (limits from the DUT, not the test)

Define the DUT's characteristics once in `parts/<id>.yaml`, then bind a sidecar limit to a
characteristic instead of hard-coding bounds:

```yaml
# test_output.yaml
tests:
  test_output:
    limits:
      output_voltage: {characteristic: output_voltage}   # resolves from the part spec
```

Now `verify("output_voltage", x)` derives its limit from the part. Run against a part with
`pytest --part=<id>` (or via a profile).

## Rung 4 — profiles (phase-specific overrides)

Bundle session-level overrides (limits, sweeps, mocks, fixture, station_type) per facet in
`profiles/`, and select them with `--test-profile=<name>` or `--test-phase=<phase>`.

## The ladder (adopt each rung only when you want it)

| Rung | You write | You need |
|---|---|---|
| 0 | `observe(...)` / `verify(..., limit={...})` | nothing (bare `pip install`) |
| 1 | `verify("name", x)` (limit in sidecar) | a `<test>.yaml` sidecar |
| 2 | `test(psu, dmm)` + `--mock-instruments` | a station (or `litmus init --tier bringup`) |
| 3 | `verify("name", x)` (limit from spec) | a part spec + `characteristic:` in the sidecar |
| 4 | `--test-profile` / `--test-phase` | profiles |

Pull the exact schema for any rung on demand: `litmus refs show routing | verify | observe | mocks | profiles | tiers` — start with `routing`, which maps a request to the right verb + rung.
