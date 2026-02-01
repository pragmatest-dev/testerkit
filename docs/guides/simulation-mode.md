# Mock Mode

Run tests without hardware using Litmus mock instruments.

## Quick Start

Add `--mock-instruments` to run without hardware:

```bash
pytest tests/ --station-config=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
```

The same test code works with real hardware or mocks.

## Configuring Mock Values

### Station-Level (Default Values)

Define default mock values in your station config:

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  name: "Production Bench 1"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
      current: 0.1
      resistance: 1000

  psu:
    type: power_supply
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
      current: 0.5
```

### Test-Level (Override for Specific Tests)

Override mock values for a specific test:

```yaml
# tests/config.yaml
test_output_voltage:
  _mock:
    dmm.measure_voltage: 3.31
    psu.measure_current: 0.5
  limits:
    test_output_voltage:
      low: 3.2
      high: 3.4
      nominal: 3.3
      units: V
```

The `_mock` key maps `instrument.method` to return values.

### Vector-Level (Different Values per Condition)

For parametrized tests with different outputs per condition:

```yaml
# tests/config.yaml
test_load_regulation:
  vectors:
    - load: 0.1
      _mock:
        dmm.measure_voltage: 3.32
        psu.measure_current: 0.15
    - load: 0.5
      _mock:
        dmm.measure_voltage: 3.30
        psu.measure_current: 0.55
    - load: 0.8
      _mock:
        dmm.measure_voltage: 3.28
        psu.measure_current: 0.85
  limits:
    test_load_regulation:
      low: 3.2
      high: 3.4
      units: V
```

Each vector gets its own mock values, simulating realistic output changes.

## Mock Value Priority

When running with `--mock-instruments`, values are resolved in order:

1. **Vector-level `_mock`** — Specific to this test vector
2. **Test-level `_mock`** — Constant for all vectors in this test
3. **Limit `nominal`** — From the measurement's limit config
4. **Station `mock_config`** — Default for this instrument
5. **Zero** — Default if nothing else configured

This allows realistic tests where:
- Simple tests use limit nominal values automatically
- Complex tests configure per-vector outputs for realistic sweeps

## CI/CD Configuration

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --station-config=stations/bench_1.yaml \
      --mock-instruments \
      --dut-serial=CI-TEST \
      -v
```

## Per-Instrument Mock Control

Mock individual instruments while using real hardware for others:

```yaml
# stations/mixed_bench.yaml
station:
  id: mixed_bench
  name: "Mixed Mode Bench"

instruments:
  psu:
    type: psu
    resource: "GPIB0::5::INSTR"
    # No mock flag - uses real hardware

  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true              # Always mock this instrument
    mock_config:
      voltage: 3.3

  eload:
    type: eload
    resource: "TCPIP::192.168.1.101::INSTR"
    # No mock flag - uses real hardware
```

Run without `--mock-instruments`:

```bash
pytest tests/ --station-config=stations/mixed_bench.yaml --dut-serial=SN001
```

- `psu` and `eload` connect to real hardware
- `dmm` uses mock (returns 3.3V)

This is useful when:
- One instrument is unavailable or broken
- Testing instrument-specific edge cases
- Simulating hard-to-reproduce conditions

## Environment Variable

Set `LITMUS_MOCK_INSTRUMENTS=1` to enable mock mode without the CLI flag:

```bash
export LITMUS_MOCK_INSTRUMENTS=1
pytest tests/ --station-config=stations/bench_1.yaml --dut-serial=CI-TEST
```

## The mock_instruments Fixture

Access the mock flag in custom fixtures:

```python
@pytest.fixture
def my_custom_setup(mock_instruments):
    """Setup that behaves differently in mock mode."""
    if mock_instruments:
        # Skip hardware initialization
        yield {"mode": "mock"}
    else:
        # Real hardware setup
        yield {"mode": "hardware"}
```

## Best Practices

### 1. Use Realistic Values

Configure mock values close to real measurements:

```yaml
# Good: realistic values
mock_config:
  voltage: 3.31
  current: 0.102

# Bad: obviously fake
mock_config:
  voltage: 1234
  current: 5678
```

### 2. Test Edge Cases

Use vector-level `_mock` to simulate failure conditions:

```yaml
test_out_of_range_handling:
  vectors:
    - condition: normal
      _mock:
        dmm.measure_voltage: 3.3
    - condition: high
      _mock:
        dmm.measure_voltage: 99.99  # Way out of spec
```

### 3. Match Limit Nominals

For simple tests, configure mock values to match limit nominals:

```yaml
test_voltage:
  _mock:
    dmm.measure_voltage: 3.3  # Matches nominal
  limits:
    test_voltage:
      low: 3.135
      high: 3.465
      nominal: 3.3
      units: V
```

## Hardware Tests

For tests that require real hardware:

```python
import pytest

@pytest.mark.hardware
def test_real_measurement(instruments):
    """Test requiring real hardware."""
    dmm = instruments["dmm"]
    v = dmm.measure_voltage()
    assert isinstance(v, float)
```

Run hardware tests separately:

```bash
pytest -m hardware           # Only hardware tests
pytest -m "not hardware"     # Skip hardware tests
```

## Next Steps

- [Writing Tests](writing-tests.md) — Test patterns
- [Configuring Stations](configuring-stations.md) — Station configuration
- [Adding Instruments](adding-instruments.md) — Custom drivers
