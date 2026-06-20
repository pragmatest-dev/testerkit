# Step 7: Real Instruments

**Goal:** Connect to real hardware with the ability to mock when unavailable.

## What You'll Build

A test that works with real instruments OR in mock mode using the same code.

## Station Configuration

Define your test station (the bench where you test). The `driver:` value points at a [PyMeasure](https://pymeasure.readthedocs.io/) (or [PyVISA](https://pyvisa.readthedocs.io/)) driver class:

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Production Bench 1"
location: "Lab A, Position 1"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_dc_voltage: 3.31    # Method-keyed return for mock mode
      measure_current: 0.1

  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config:
      measure_voltage: 5.0
      measure_current: 0.1
```

This defines:
- A station identity and location
- A DMM at a TCP/IP address with mock values
- A PSU on GPIB with mock values

## Instrument Role Fixtures

When you run with `--station`, Litmus auto-registers each instrument role as a pytest fixture. Use them directly as function parameters:

```python
# tests/test_power.py
def test_output_voltage(psu, dmm, measure):
    """Instrument roles from station config are auto-registered as fixtures."""
    psu.set_voltage(5.0)
    psu.enable_output()

    measure("output_voltage", dmm.measure_dc_voltage())
```

Run with real hardware:
```bash
pytest tests/ --station=stations/bench_1.yaml --uut-serial=SN001
```

## Running with Mock Instruments

When hardware isn't available, add `--mock-instruments`:

```bash
pytest tests/ --station=stations/bench_1.yaml --mock-instruments --uut-serial=SIM001
```

The **same test code** works in both modes.

## How Mock Mode Works

When `--mock-instruments` is set:
1. Mock instruments are used instead of real drivers
2. Responses come from `mock_config` values in station config
3. No hardware required

## Per-Test Mock Values

For tests that need specific mock values, use `litmus_mocks` in the sidecar:

```yaml
# tests/test_voltage.yaml
mocks:
  - {target: dmm.measure_dc_voltage, return_value: 3.31}
  - {target: psu.measure_current, return_value: 0.5}
limits:
  test_output_voltage: {low: 3.2, high: 3.4, unit: V}
```

## Mock Value Priority

When running with `--mock-instruments`, values are resolved in order:

1. **`litmus_mocks` marker** — Per-test mock values (sidecar or inline)
2. **Station `mock_config`** — Default for this instrument
3. **Zero** — If nothing else configured

## CI/CD Configuration

In CI, always run with `--mock-instruments`:

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --station=stations/bench_1.yaml \
      --mock-instruments \
      --uut-serial=CI-TEST \
      -v
```

## VISA Address Formats

VISA (Virtual Instrument Software Architecture) is the cross-vendor standard for addressing test instruments — every PyVISA-backed driver uses one of these resource strings.

| Type | Format | Example |
|------|--------|---------|
| TCP/IP | `TCPIP::host::INSTR` | `TCPIP::192.168.1.100::INSTR` |
| GPIB | `GPIB0::address::INSTR` | `GPIB0::5::INSTR` |
| USB | `USB0::vid::pid::serial::INSTR` | `USB0::0x2A8D::0x0101::MY12345::INSTR` |
| Serial | `ASRL/dev/ttyUSB0::INSTR` | `ASRL/dev/ttyUSB0::INSTR` |

## Discovering Instruments

Litmus ships a CLI that walks the VISA bus and prints what it finds:

```bash
litmus discover
```

Sample output:

```
Scanning for instruments...

VISA: Found 3 instrument(s)
------------------------------------------------------------
  Keysight 34461A (SN: MY12345678) (TCPIP::192.168.1.100::INSTR)
  Keysight E36312A (SN: MY87654321) (TCPIP::192.168.1.101::INSTR)
  Keithley 2400 (SN: SN98765) (GPIB0::22::INSTR)

Next: litmus station init
```

Each line shows the manufacturer + model + serial + VISA resource
string (the value that goes in `resource:` above). The MCP tool
`litmus_discover()` returns the same instruments as JSON, with
extra structured fields (`catalog_ref`, separated manufacturer /
model / serial / type) that the CLI doesn't print.

To walk a station scaffold interactively — pick a role per
discovered instrument and write the YAML — follow the CLI's
prompt:

```bash
litmus station init
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No module named 'pymeasure.instruments...'` | Driver package not installed. Litmus falls back to raw PyVISA. | `pip install pymeasure` (or `uv add pymeasure`). Verify the full import path in `driver:`. |
| Instrument not responding / timeout | PyVISA can't reach the instrument | Verify resource string with `litmus discover`. Check network / GPIB cables. |
| `instrument identity mismatch` warning | Instrument serial or model doesn't match the asset YAML | Open `instruments/<instrument-id>.yaml` (filename is the instrument ID — e.g. `dmm_MY12345.yaml`, not the station role) and update the manufacturer / model / serial fields, or accept the mismatch during development. |
| `CALIBRATION EXPIRED` warning | Cal due date has passed in the instrument asset YAML | Update the `calibration.due_date` field, or accept the warning for development. |
| Mock-mode results stamped as `development` even though you asked for `--test-phase=validation` | When `--mock-instruments` is on, the platform silently demotes `test_phase` to `development` on the result rows. The run still passes; the data is just tagged as dev. | This is by design — mock data shouldn't pollute validation metrics. Run against real hardware (drop `--mock-instruments`) to keep `validation` in the data. |
| Fixture `psu` not found (or any role) | Station not loaded, or role not defined | Check `--station` flag points to the right file. Verify the role exists in your station YAML. |

## Complete Example

**stations/bench_1.yaml:**
```yaml
id: bench_1
name: "Production Bench 1"
location: "Lab A"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_dc_voltage: 3.31
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config:
      measure_voltage: 5.0
```

**tests/test_power.yaml** (sidecar):
```yaml
limits:
  output_voltage:
    low: 3.135
    high: 3.465
    nominal: 3.3
    unit: V
mocks:
  - target: dmm.measure_dc_voltage
    return_value: 3.31
```

**tests/test_power.py:**
```python
def test_output_voltage(psu, dmm, measure):
    """Works with real hardware OR mock mode."""
    psu.set_voltage(5.0)
    psu.set_current_limit(1.0)
    psu.enable_output()

    voltage = dmm.measure_dc_voltage()

    psu.disable_output()
    measure("output_voltage", voltage)
```

**Run with hardware:**
```bash
pytest tests/ --station=stations/bench_1.yaml --uut-serial=SN12345
```

**Run with mocks:**
```bash
pytest tests/ --station=stations/bench_1.yaml --mock-instruments --uut-serial=SIM001
```

## What You Learned

- Station configuration with instruments and `mock_config`
- Instrument role fixtures from station config (e.g. `psu`, `dmm`)
- `--mock-instruments` flag for hardware-free testing
- Per-test mock values with `mocks` in the sidecar YAML
- VISA address formats

## Continue

How does Litmus know which station can test which part?

← [Step 6: Part Specifications](06-specifications.md)  |  [Step 8: Capability Matching →](08-capabilities.md)
