# Example: Power Converter Test Suite

A complete example of testing a DC-DC power converter from spec to results.

## Overview

This example shows:
- Product specification
- Station configuration
- Fixture definition
- Test sequence
- Test code
- Running and viewing results

## The Product

A simple 5V to 3.3V DC-DC converter with:
- Input voltage: 5V ±10%
- Output voltage: 3.3V ±5%
- Output current: up to 1A

## Project Structure

```
power_converter_test/
├── products/                       # WHAT you're testing
│   └── dc_converter/
│       └── spec.yaml
├── stations/                       # WHERE you test
│   └── bench_1.yaml
├── fixtures/                       # HOW pins connect to instruments
│   └── dc_converter_fixture.yaml
├── instruments/                    # Custom drivers (optional)
├── sequences/                      # Test execution order
│   └── production_test.yaml
├── tests/                          # Test code
│   ├── config.yaml                 # CONDITIONS + LIMITS
│   ├── conftest.py
│   └── test_dc_converter.py
└── results/                        # Output (gitignored)
```

## Product Specification

```yaml
# products/dc_converter/spec.yaml
product:
  id: dc_converter
  name: "5V to 3.3V DC-DC Converter"
  revision: "A"
  datasheet: "docs/DC-CONV-001.pdf"

pins:
  VIN:
    name: "J1.1"
    net: "VIN_5V"
    type: power
  VOUT:
    name: "J1.2"
    net: "VOUT_3V3"
    type: signal
  GND:
    name: "J1.3"
    net: "GND"
    type: ground
  EN:
    name: "J1.4"
    net: "ENABLE"
    type: signal

characteristics:
  input_voltage:
    direction: input
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VIN]
    datasheet_ref: "Section 4.1"
    conditions:
      - nominal: 5.0
        tolerance_pct: 10

  output_voltage:
    direction: output
    domain: voltage
    signal_types: [dc]
    units: V
    pins: [VOUT]
    datasheet_ref: "Section 4.2"
    conditions:
      - nominal: 3.3
        tolerance_pct: 5
        load_ma: 0

      - nominal: 3.3
        tolerance_pct: 5
        load_ma: 500

      - nominal: 3.3
        tolerance_pct: 6
        load_ma: 1000

  output_current:
    direction: output
    domain: current
    signal_types: [dc]
    units: A
    pins: [VOUT]
    datasheet_ref: "Section 4.3"
    conditions:
      - nominal: 0
        limit_low: 0
        limit_high: 1.0

test_requirements:
  verify_output_no_load:
    characteristic_ref: output_voltage
    conditions:
      load_ma: 0
    guardband_pct: 10
    priority: 1

  verify_output_half_load:
    characteristic_ref: output_voltage
    conditions:
      load_ma: 500
    guardband_pct: 10
    priority: 2

  verify_output_full_load:
    characteristic_ref: output_voltage
    conditions:
      load_ma: 1000
    guardband_pct: 10
    priority: 3
```

## Station Configuration

```yaml
# stations/bench_1.yaml
station:
  id: bench_1
  name: "Production Bench 1"
  location: "Lab A, Bay 1"

supported_phases:
  - production
  - debug

instruments:
  psu:
    type: power_supply
    resource: "TCPIP::192.168.1.101::INSTR"

  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.102::INSTR"

  eload:
    type: electronic_load
    resource: "TCPIP::192.168.1.103::INSTR"
```

## Fixture Definition

```yaml
# fixtures/dc_converter_fixture.yaml
fixture:
  id: dc_converter_fixture
  name: "DC Converter Test Fixture"
  product_id: dc_converter

points:
  VIN:
    dut_pin: VIN
    instrument: psu
    instrument_channel: "1"

  VOUT:
    dut_pin: VOUT
    instrument: dmm

  LOAD:
    dut_pin: VOUT
    instrument: eload
    instrument_channel: "1"

  GND:
    dut_pin: GND
    instrument: psu
    instrument_channel: "GND"
```

## Test Configuration

```yaml
# tests/config.yaml
test_startup:
  limits:
    startup_time:
      low: 0
      high: 10
      units: ms

test_output_no_load:
  limits:
    output_voltage:
      low: 3.152
      high: 3.449
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ load_ma=0, guardband=10%"

test_output_half_load:
  limits:
    output_voltage:
      low: 3.152
      high: 3.449
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ load_ma=500, guardband=10%"

test_output_full_load:
  limits:
    output_voltage:
      low: 3.119
      high: 3.481
      nominal: 3.3
      units: V
      spec_ref: "output_voltage @ load_ma=1000, guardband=10%"

test_load_sweep:
  vectors:
    expand: range
    load_ma:
      start: 0
      stop: 1000
      step: 100
  limits:
    output_voltage:
      low: 3.1
      high: 3.5
      units: V
```

## Test Sequence

```yaml
# sequences/production_test.yaml
sequence:
  id: dc_converter_production
  name: "DC Converter Production Test"
  product_family: dc_converter
  test_phase: production
  required_fixture: dc_converter_fixture

steps:
  - name: startup
    test: test_dc_converter.test_startup
    description: "Verify startup time"

  - name: no_load
    test: test_dc_converter.test_output_no_load
    description: "Verify output voltage at no load"

  - name: half_load
    test: test_dc_converter.test_output_half_load
    description: "Verify output voltage at 500mA"

  - name: full_load
    test: test_dc_converter.test_output_full_load
    description: "Verify output voltage at 1A"
    retry:
      max_attempts: 2
      delay_seconds: 0.5

  - name: load_sweep
    test: test_dc_converter.test_load_sweep
    description: "Characterize output across load range"
```

## Test Code

```python
# tests/test_dc_converter.py
import time
from litmus.execution import litmus_test


@litmus_test
def test_startup(vector, pins):
    """Measure startup time."""
    # Ensure output is off
    pins["VIN"].disable_output()
    time.sleep(0.1)

    # Apply input
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].set_current_limit(0.5)

    # Time the startup
    start = time.perf_counter()
    pins["VIN"].enable_output()

    # Wait for output to stabilize
    while True:
        voltage = pins["VOUT"].measure_voltage()
        if float(voltage) > 3.0:
            break
        if time.perf_counter() - start > 0.1:  # Timeout
            break
        time.sleep(0.001)

    startup_time_ms = (time.perf_counter() - start) * 1000
    return {"startup_time": startup_time_ms}


@litmus_test
def test_output_no_load(vector, pins):
    """Verify output voltage with no load."""
    pins["LOAD"].set_current(0)
    time.sleep(0.05)
    return pins["VOUT"].measure_voltage()


@litmus_test
def test_output_half_load(vector, pins):
    """Verify output voltage at 500mA."""
    pins["LOAD"].set_current(0.5)
    time.sleep(0.05)
    return pins["VOUT"].measure_voltage()


@litmus_test
def test_output_full_load(vector, pins):
    """Verify output voltage at 1A."""
    pins["LOAD"].set_current(1.0)
    time.sleep(0.05)
    voltage = pins["VOUT"].measure_voltage()
    pins["LOAD"].set_current(0)  # Reduce thermal stress
    return voltage


@litmus_test
def test_load_sweep(vector, pins):
    """Characterize output across load range."""
    load_ma = vector["load_ma"]
    pins["LOAD"].set_current(load_ma / 1000)
    time.sleep(0.02)
    return pins["VOUT"].measure_voltage()
```

## pytest Configuration

```python
# tests/conftest.py
import pytest


def pytest_addoption(parser):
    parser.addoption("--dut-serial", action="store", required=True)
    parser.addoption("--station", action="store", default="bench_1")
    parser.addoption("--mock-instruments", action="store_true")


@pytest.fixture
def dut_serial(request):
    return request.config.getoption("--dut-serial")


@pytest.fixture
def station_id(request):
    return request.config.getoption("--station")


@pytest.fixture
def simulate(request):
    return request.config.getoption("--mock-instruments")
```

## Running Tests

### Development (Simulated)

```bash
pytest tests/ \
  --station=bench_1 \
  --mock-instruments \
  --dut-serial=SIM001 \
  -v
```

### Production

```bash
pytest tests/ \
  --station=bench_1 \
  --dut-serial=SN12345 \
  --test-phase=production \
  -v
```

## Viewing Results

### CLI

```bash
litmus runs
# ID        DUT         Station    Outcome   Time
# abc123    SN12345     bench_1    PASS      2026-01-30 10:23:45

litmus show abc123
# Test Run: abc123
# DUT: SN12345
# Station: bench_1
# Outcome: PASS
#
# Steps:
#   startup: PASS (startup_time=2.3ms)
#   no_load: PASS (output_voltage=3.31V)
#   half_load: PASS (output_voltage=3.29V)
#   full_load: PASS (output_voltage=3.27V)
#   load_sweep: PASS (11 vectors)
```

### Operator UI

```bash
litmus serve
# Open http://localhost:8000
```

### Python

```python
from litmus import LitmusClient

client = LitmusClient()
measurements = client.get_measurements("abc123")

for m in measurements:
    print(f"{m['step_name']}/{m['measurement_name']}: {m['value']} {m['units']}")
```

## CI/CD Configuration

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -e .

      - name: Run tests
        run: |
          pytest tests/ \
            --station=ci_station \
            --mock-instruments \
            --dut-serial=CI-TEST \
            -v
```

## Key Takeaways

1. **Spec drives everything** — Limits come from product spec
2. **Fixtures abstract wiring** — Tests use pin names, not instruments
3. **Simulation enables CI** — Same tests run everywhere
4. **Results are queryable** — Parquet storage for analytics
5. **Configuration is versioned** — All YAML in source control

## Next Steps

- [Tutorial](../tutorial/index.md) — Step-by-step learning
- [Writing Tests](../guides/writing-tests.md) — More patterns
- [Configuration Reference](../reference/configuration.md) — All options
