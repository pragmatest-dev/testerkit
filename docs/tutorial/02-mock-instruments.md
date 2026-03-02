# Step 2: Running Without Hardware

**Goal:** Run tests without real instruments using mock mode.

## The Problem

Real hardware testing requires real instruments. But during development:

- Instruments may not be available
- CI/CD runs on servers without hardware
- Iteration should be fast

Litmus solves this with the `--mock-instruments` flag.

## Station Configuration

Define your instruments in a station config:

```yaml
# stations/my_station.yaml
id: my_station
name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31      # Value returned in mock mode
      current: 0.1

  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
```

The `mock_config` section defines what values mock instruments return.

## Running in Mock Mode

Add `--mock-instruments` to run without hardware:

```bash
pytest tests/ --station-config=stations/my_station.yaml --mock-instruments -v
```

The **same test code** works with real hardware or mocks.

## A Simple Test

Instrument roles from the station config are auto-registered as pytest fixtures. Use them directly -- no conftest boilerplate:

```python
# tests/test_voltage.py
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(context, dmm, psu):
    """Measure output voltage. dmm and psu are auto-registered from station config."""
    psu.set_voltage(5.0)
    psu.enable_output()

    return dmm.measure_voltage()  # Returns 3.31 in mock mode
```

Run it:

```bash
# With mock instruments
pytest tests/test_voltage.py --station-config=stations/my_station.yaml --mock-instruments -v

# With real hardware (when available)
pytest tests/test_voltage.py --station-config=stations/my_station.yaml -v
```

## Per-Test Mock Values

For tests that need specific mock values, define `mocks` on the sequence step:

```yaml
# sequences/my_sequence.yaml
steps:
  - id: output_voltage
    test: tests/test_voltage.py::test_output_voltage
    mocks:
      dmm.measure_voltage: 3.31
      psu.measure_current: 0.5
    limits:
      test_output_voltage:
        low: 3.2
        high: 3.4
        units: V
```

The `mocks` key maps `instrument.method` to return values. We'll cover sequences fully in [Step 5](05-configuration.md).

## Per-Vector Mock Values

For sweeps with different outputs per condition:

```yaml
# sequences/my_sequence.yaml
steps:
  - id: load_sweep
    test: tests/test_voltage.py::test_load_sweep
    vectors:
      - load: 0.1
        _mocks:
          dmm.measure_voltage: 3.32
      - load: 0.5
        _mocks:
          dmm.measure_voltage: 3.30
      - load: 0.8
        _mocks:
          dmm.measure_voltage: 3.28
    limits:
      test_load_sweep:
        low: 3.2
        high: 3.4
        units: V
```

Each vector gets its own mock values, simulating realistic output changes. Note the `_mocks` key (underscore prefix) inside vector dicts.

## Mock Value Priority

When running with `--mock-instruments`, values are resolved in order:

1. **Vector-level `_mocks`** — Specific to this test vector
2. **Step-level `mocks`** — Constant for all vectors in this step
3. **Station `mock_config`** — Default for this instrument
4. **Zero** — If nothing else configured

## CI/CD Configuration

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --station-config=stations/ci_station.yaml \
      --mock-instruments \
      --dut-serial=CI-TEST \
      -v
```

## What You Learned

- `--mock-instruments` flag for hardware-free testing
- Station `mock_config` for default mock values
- Sequence step `mocks` for per-test/per-vector values
- Same test code works with real hardware or mocks

## Next Step

Now let's use the @litmus_test decorator to check limits automatically.

[Step 3: The @litmus_test Decorator →](03-decorator.md)
