# From Mocks to Hardware

You ran `litmus init --starter && pytest` and everything passed with mock instruments. Now you want to connect real hardware. This guide bridges that gap.

## A. Discover What's on Your Bench

Scan for connected instruments:

```bash
litmus discover
```

Example output:

```
Found 3 instruments:

  TCPIP::192.168.1.100::INSTR
    Keysight Technologies,34461A,MY12345678,A.02.14
    Type: dmm (from catalog)

  TCPIP::192.168.1.101::INSTR
    Keysight Technologies,E36312A,MY87654321,A.01.05
    Type: psu (from catalog)

  GPIB0::22::INSTR
    Keithley Instruments,2400,SN98765,C04
    Type: smu (from catalog)
```

Each line shows the VISA resource string (what you put in station config) and the instrument identity. Common address formats:

| Bus | Format | Example |
|-----|--------|---------|
| LAN/LXI | `TCPIP::<ip>::INSTR` | `TCPIP::192.168.1.100::INSTR` |
| GPIB | `GPIB0::<addr>::INSTR` | `GPIB0::22::INSTR` |
| USB | `USB0::<vid>::<pid>::<serial>::INSTR` | `USB0::0x2A8D::0x0101::MY12345::INSTR` |

## B. Create Your Real Station Config

The interactive command walks you through role assignment:

```bash
litmus station init
```

Or create the file manually. Here's the key insight — a real station config isn't "mock OR real", it's **real with mock fallback**:

```yaml
# STARTER (mock-only)              # REAL BENCH (with mock fallback)
# stations/starter_station.yaml    # stations/my_bench.yaml

# instruments:                     instruments:
#   dmm:                             dmm:
#     type: dmm                        type: dmm
#     resource: TCPIP::...::INSTR      driver: pymeasure.instruments.keysight.Keysight34461A
#     mock: true                        resource: "TCPIP::192.168.1.100::INSTR"
#     mock_config:                      mock_config:        # kept for --mock-instruments
#       measure_dc_voltage: 3.3           measure_dc_voltage: 3.31
```

The differences:

| Field | Starter | Real bench |
|-------|---------|------------|
| `mock: true` | Present — forces mock mode | Absent — uses real hardware by default |
| `driver:` | Absent — not needed for mocks | Present — PyMeasure class for high-level API |
| `mock_config:` | Defines mock returns | **Still present** — used when you pass `--mock-instruments` |

Without `mock: true`, Litmus connects to real hardware. The `--mock-instruments` CLI flag overrides this per-run, using the `mock_config` values. This means you can run the same station config in CI (with `--mock-instruments`) and on the bench (without it).

A minimal real station (no driver, raw PyVISA):

```yaml
id: my_bench
name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_dc_voltage: 3.31
```

With a PyMeasure driver (high-level methods like `.measure_dc_voltage()`):

```yaml
id: my_bench
name: "My Test Bench"

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
    resource: "TCPIP::192.168.1.101::INSTR"
    mock_config:
      set_voltage: null
      enable_output: null
      measure_voltage: 5.0
      measure_current: 0.25
```

## C. Run — Verify, Then Connect

Start with mocks to confirm your config is valid:

```bash
pytest --station=my_bench --mock-instruments
```

Once that passes, remove the flag to connect to real hardware:

```bash
pytest --station=my_bench
```

Your test code doesn't change at all. The `instruments` fixture handles mock vs. real based on the config and CLI flags.

## D. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No module named 'pymeasure.instruments...'` | Driver package not installed. Litmus falls back to raw PyVISA. | `pip install pymeasure` (or `uv add pymeasure`). Verify the full import path in `driver:`. |
| Instrument not responding / timeout | PyVISA can't reach the instrument | Verify resource string with `litmus discover`. Check network/GPIB cables. |
| "instrument identity mismatch" warning | Instrument serial or model doesn't match the YAML | Update `instruments/{role}.yaml` with the correct serial/model, or ignore during development. |
| "CALIBRATION EXPIRED" warning | Cal due date has passed in instrument YAML | Update the cal due date, or accept for development. |
| "Mock instruments not allowed for test_phase='validation'" | Phase enforcement blocks mocks in validation/production | Remove `--mock-instruments`, or set `test_phase: dev` in your sequence. |
| Fixture `psu` not found (or any role) | Station not loaded or role not defined | Check `--station` flag points to the right file. Verify the role exists in your station YAML. |

## What to Do Next

- [Step 7: Real Instruments](07-real-instruments.md) — Full tutorial on instrument drivers, identity verification, and calibration tracking
- [CLI Reference](../reference/cli.md) — All `litmus discover` and `litmus station init` options
