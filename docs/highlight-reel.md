# Litmus Highlight Reel: Concepts to Code

What does user code actually look like? This walks through the key patterns
with real snippets from the demo test suite (a 5V-to-3.3V power converter).

---

## 1. The Simplest Test

Set up, measure, log. Three fixtures (`context`, `spec`, `logger`) do the work.

```python
import pytest

@pytest.mark.parametrize("vin", [5.0])
@pytest.mark.litmus_limits({
    "output_voltage": {
        "low": 3.234, "high": 3.366, "nominal": 3.3, "units": "V",
    }
})
def test_output_voltage_no_load(vin, context, logger, psu: PSU, dmm: DMM, mocker):
    mocker.patch.object(dmm, "measure_dc_voltage", return_value=3.3)

    psu.set_voltage(vin)
    psu.set_current_limit(0.1)
    psu.enable_output()

    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

That's the whole test. The framework:
- Injects instruments (`psu`, `dmm`) from station config
- Iterates vectors (here just one: vin=5.0)
- Checks the logged value against the limit resolved from the marker
- Records everything to Parquet with traceability fields
- Uses mock values when `--mock-instruments` is active

Markers can also live in a sidecar `test_<module>.yaml` file — same fields,
no Python decoration.

---

## 2. Multiple Measurements

Call `logger.measure` once per measurement. Each gets checked against its own limit.

```python
@pytest.mark.parametrize("vin, load_current", [(5.0, 0.5)])
@pytest.mark.litmus_limits({
    "input_power":  {"low": 0, "high": 5.0, "units": "W"},
    "output_power": {"low": 0, "high": 3.0, "units": "W"},
    "efficiency":   {"low": 60, "high": 100, "units": "%"},
})
def test_power_analysis(vin, load_current, logger, psu: PSU, dmm: DMM, eload: ELoad):
    psu.set_voltage(vin)
    psu.enable_output()
    eload.set_current(load_current)
    eload.enable()

    v_in = float(psu.measure_voltage())
    i_in = float(psu.measure_current())
    v_out = float(dmm.measure_dc_voltage())
    p_in = v_in * i_in
    p_out = v_out * 0.5

    logger.measure("input_power", p_in)
    logger.measure("output_power", p_out)
    logger.measure("efficiency", (p_out / p_in * 100) if p_in > 0 else 0)
```

---

## 3. Sweep with Change Detection

25 vectors from a Cartesian product. Only reconfigure the PSU when VIN actually changes.

```python
@pytest.mark.parametrize("vin", [4.5, 4.75, 5.0, 5.25, 5.5])
@pytest.mark.parametrize("load_current", [0.1, 0.3, 0.5, 0.7, 0.8])
@pytest.mark.litmus_limits({
    "output_voltage": {"low": 3.1, "high": 3.5, "nominal": 3.3, "units": "V"}
})
def test_load_sweep(vin, load_current, context, logger, psu: PSU, dmm: DMM, eload: ELoad):
    if context.changed("vin"):          # only when VIN changes
        psu.set_voltage(vin)
        psu.enable_output()

    eload.set_current(load_current)
    eload.enable()
    logger.measure("output_voltage", dmm.measure_dc_voltage())
    eload.disable()
```

For range-style sweeps, sidecar YAML supports `"start:stop:step"` strings
anywhere a list is expected — see [Vector Expansion](guides/vector-expansion.md).
Native parametrize is the primary in-code API; sidecar vectors are the
operator-editable alternative.

---

## 4. Streaming Measurements

Monitor stability over time. Each `logger.measure` records a measurement and
checks it against limits immediately. If you need to keep collecting samples
after a failure, use `logger.measure(..., raise_on_fail=False)`.

```python
@pytest.mark.parametrize("vin, sample_count", [(5.0, 5)])
@pytest.mark.litmus_limits({
    "voltage": {"low": 3.25, "high": 3.35, "nominal": 3.3, "units": "V"}
})
def test_stability_over_time(vin, sample_count, logger, psu: PSU, dmm: DMM, eload: ELoad):
    psu.set_voltage(vin)
    psu.enable_output()
    eload.set_current(0.5)
    eload.enable()

    for i in range(sample_count):
        logger.measure("voltage", float(dmm.measure_dc_voltage()), allow_repeat=True)
        time.sleep(0.1)

    eload.disable()
```

---

## 5. Inline Limits, No Markers

Sometimes you want the limit inline with the measurement — no marker, no
sidecar, just a `Limit` object in the test body. Results still go to Parquet
with full traceability.

```python
from litmus.models import Limit

def test_basic_measurement(psu, dmm, logger):
    limit = Limit(low=3.2, high=3.4, nominal=3.3, units="V")

    psu.set_voltage(5.0)
    psu.enable_output()

    vout = float(dmm.measure_dc_voltage())

    logger.measure(
        name="output_voltage",
        value=vout,
        limit=limit,
        dut_pin="TP_VOUT",
    )
```

---

## 6. Configuration Files

### Project config (`litmus.yaml`)

```yaml
name: demo
default_station: demo_station_001
default_fixture: power_board_fixture
mock_instruments: true
```

### Station config — what's on this bench

```yaml
id: demo_station_001
name: Demo Test Station
instruments:
  psu:
    driver: demo.drivers.PSU
    resource: TCPIP::192.168.1.101::INSTR
    mock: true
    mock_config:
      measure_voltage: 5.0
      measure_current: 0.25
  dmm:
    driver: demo.drivers.DMM
    resource: TCPIP::192.168.1.102::INSTR
    mock: true
    mock_config:
      measure_dc_voltage: 3.3
  eload:
    driver: demo.drivers.ELoad
    resource: TCPIP::192.168.1.103::INSTR
    mock: true
```

Tests ask for `psu`, `dmm`, `eload` by role. Station config maps roles to
actual instruments. Swap benches by swapping the station file.

### Product spec — limits derived from this, not from code

```yaml
id: power_board
part_number: DPB-001
name: Demo Power Board Rev A
characteristics:
  output_voltage:
    function: dc_voltage
    units: V
    pin: TP_VOUT
    specs:
    - when: {temperature: {min: 25, max: 25}, load: {min: 0.1, max: 0.1}}
      value: 3.3
      accuracy: {pct_reading: 2.0}
    - when: {temperature: {min: 85, max: 85}, load: {min: 0.8, max: 0.8}}
      value: 3.3
      accuracy: {pct_reading: 4.0}
```

The characteristic name `output_voltage` is the link. The test's limit config
references it via `ref`:

```yaml
# In the sequence step or decorator config
limits:
  output_voltage:
    ref: output_voltage          # ← matches product characteristic name
```

The framework looks up `output_voltage` in the product spec, finds
`value: 3.3, accuracy: {pct_reading: 2.0}`, and derives `low: 3.234,
high: 3.366` automatically. Change the spec accuracy from 2% to 1% — limits
tighten everywhere, no test code or limit YAML touched.

A test engineer changes this file via PR. No Python needed.

### Fixture config — the wiring diagram as config

```yaml
id: power_board_fixture_v1
product_id: power_board
points:
  vin_supply:
    dut_pin: J1_VIN
    instrument: psu
    instrument_channel: CH1
    instrument_terminal: hi
  vout_measure:
    dut_pin: TP_VOUT
    instrument: dmm
    instrument_channel: "1"
    instrument_terminal: hi
```

DUT pin `TP_VOUT` maps to DMM channel 1. Full traceability from measurement
to physical test point.

### Sequence — test ordering and orchestration

```yaml
id: power_board_smoke
name: Power Board - Smoke Test
product_family: power_board
test_phase: production
steps:
- id: basic_power
  test: tests/test_power_board.py::test_output_voltage_no_load
  vectors:
  - vin: 5.0
  limits:
    output_voltage:
      low: 3.234
      high: 3.366
      nominal: 3.3
      units: V
- id: load_test
  test: tests/test_power_board.py::test_output_voltage_full_load
  aliases:
    dmm: fast_dmm              # swap in a different DMM for this step
  skip_on: [basic_power]       # skip if power-up failed
  retry:
    max_attempts: 3
    delay_seconds: 0.5
```

Config resolution: sequence step overrides decorator defaults.
Non-developers can tune limits, swap instruments, add retry — no code changes.

### Same code, different phases

Because config lives outside the test functions, the same code runs across
test phases — just point at a different sequence:

```bash
pytest tests/ --sequence=power_board_validation   # 88 vectors, tight limits
pytest tests/ --sequence=power_board_production    # 5 vectors, fast screening
pytest tests/ --sequence=power_board_debug         # 1 vector, collect everything
```

Each sequence selects which tests to run, with what vectors, limits, retry
policy, and instrument aliases. The Python test files never change.

---

## 7. Config Lives Outside of Code

This is the key insight: **the test function has zero hardcoded values**.

```python
def test_output_voltage_no_load(context, logger, psu: PSU, dmm: DMM):
    psu.set_voltage(context.get_param("vin", 5.0))
    psu.set_current_limit(0.1)
    psu.enable_output()
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

Everything else — vectors, limits, mocks, retry, instrument mapping — comes
from config. The resolution chain:

```
sequence step config  >  markers / sidecar YAML  >  product spec
(YAML, per-phase)        (test-level defaults)      (YAML, derived)
```

### What this means in practice

**Change a limit** — edit the sequence YAML, submit a PR:
```yaml
# Before                          # After
limits:                           limits:
  output_voltage:                   output_voltage:
    low: 3.234                        low: 3.267     # tightened
    high: 3.366                       high: 3.333    # tightened
```

`git blame` shows who changed it, when, and why.

**Swap instruments per step** — aliases in the sequence:
```yaml
steps:
- id: screening
  test: tests/test_power_board.py::test_output_voltage_full_load
  aliases:
    dmm: fast_dmm          # use the fast 4.5-digit DMM for screening
- id: final_test
  test: tests/test_power_board.py::test_output_voltage_full_load
  aliases:
    dmm: dmm               # use the precise 6.5-digit DMM for final
```

Same test function, different instrument, different limits. No code change.

**Add retry without touching code:**
```yaml
steps:
- id: load_test
  test: tests/test_power_board.py::test_output_voltage_full_load
  retry:
    max_attempts: 3
    delay_seconds: 0.5
```

**Mock values for desk development:**
```yaml
mocks:
  dmm.measure_dc_voltage: 3.3
  psu.measure_current: 0.005
```

Run `pytest --mock-instruments` — tests pass at your desk without hardware.
Mock values come from config, so you can test edge cases:

```yaml
# What happens at the limit boundary?
mocks:
  dmm.measure_dc_voltage: 3.234   # exactly at low limit
```

### The three audiences

| Who | What they edit | What they never touch |
|-----|---------------|----------------------|
| **Test engineer** | Product specs, limits, vectors | Python code |
| **Bench technician** | Station config (addresses, cal dates) | Tests, limits |
| **Test developer** | Test functions, drivers | Limits, station wiring |

Each person works in their domain. PRs enforce review across boundaries.

---

## 8. Query Results with SQL

Parquet files on disk. DuckDB queries them directly. No database server.

```python
import duckdb

db = duckdb.connect()
parquet = "~/.local/share/litmus/results/runs/**/*.parquet"

# Measurement statistics
db.sql(f"""
    SELECT measurement_name, units,
           COUNT(*) AS n,
           ROUND(AVG(value), 4) AS mean,
           ROUND(STDDEV(value), 4) AS stddev
    FROM "{parquet}"
    WHERE value IS NOT NULL
    GROUP BY measurement_name, units
""")

# Process capability (Cpk) in 5 lines
db.sql(f"""
    SELECT measurement_name,
           ROUND(AVG(value), 4) AS mean,
           ROUND(STDDEV(value), 4) AS sigma,
           ROUND(MIN(low_limit), 2) AS lsl,
           ROUND(MAX(high_limit), 2) AS usl,
           ROUND(LEAST(
               (MAX(high_limit) - AVG(value)) / (3 * STDDEV(value)),
               (AVG(value) - MIN(low_limit)) / (3 * STDDEV(value))
           ), 2) AS cpk
    FROM "{parquet}"
    WHERE value IS NOT NULL AND low_limit IS NOT NULL
    GROUP BY measurement_name
""")
```

Works on an airplane. Works air-gapped. Works 20 years from now because
Parquet is an open format.

---

## 9. CLI

```bash
litmus runs                        # list recent test runs
litmus show abc123                 # terminal view of a run
litmus show abc123 -f html         # generate HTML report
litmus discover                    # scan for instruments
litmus serve --reload              # operator UI at localhost:8000
```

---

## 10. The Plugin System

All four extension points use the same pattern: ABC with `__init_subclass__`
auto-registration + entry points for third-party discovery.

```python
# A vendor ships this in their driver package
from litmus.instruments.discovery import DiscoveryProtocol

class SrsDiscovery(DiscoveryProtocol):
    name = "srs"

    def discover(self) -> list[str]:
        # scan for SRS instruments...
        return ["TCPIP::192.168.1.50::INSTR"]

    def get_info(self, resource):
        # query identity...
        return InstrumentInfo(manufacturer="SRS", model="SR830")
```

```toml
# In the vendor's pyproject.toml
[project.entry-points."litmus.discovery"]
srs = "srs_instruments.litmus_ext:SrsDiscovery"
```

User does `uv add srs-instruments`. Litmus finds the plugin automatically.

Same pattern for observers, subscribers (output formats), and transports
(file shipping). Four entry point groups:

| Group | What it extends |
|-------|----------------|
| `litmus.discovery` | Instrument scanning protocols |
| `litmus.observers` | Driver event interpretation |
| `litmus.subscribers` | Output formats (CSV, STDF, HDF5...) |
| `litmus.transports` | File shipping (S3, Azure, SFTP...) |

---

## 11. The Capability Catalog (Machine-Readable Datasheets)

Nobody publishes machine-readable instrument specs. Every datasheet is a
400-page PDF. The catalog is structured YAML extracted from datasheets — what
an instrument can do, at what accuracy, under what conditions.

### A real DMM entry (Keysight 34401A, abbreviated)

```yaml
id: keysight_34401a
manufacturer: Keysight
model: 34401A
type: dmm
capabilities:
- function: dc_voltage
  direction: input
  channels: [input]
  signals:
    voltage:
      range: {min: -1000, max: 1000, units: V}
      accuracy: {pct_reading: 0.0045, pct_range: 0.001}
      resolution: {digits: 6.5}
      specs:                         # accuracy changes by range
      - when:
          voltage: {min: -0.1, max: 0.1, units: V}
        accuracy: {pct_reading: 0.005, pct_range: 0.0035}
      - when:
          voltage: {min: -10, max: 10, units: V}
        accuracy: {pct_reading: 0.0035, pct_range: 0.0005}
  conditions:
    temperature:
      range: {min: 18, max: 28, units: degC}
```

### The four parameter categories

Every capability describes an instrument function using four typed categories:

| Category | What it is | Example |
|----------|-----------|---------|
| **signals** | What's measured or sourced | `voltage: {range: {min: -10, max: 10, units: V}}` |
| **conditions** | Operating conditions that affect accuracy | `temperature: {range: {min: 18, max: 28, units: degC}}` |
| **controls** | User-adjustable knobs | `nplc: {range: {min: 0.02, max: 100}, default: 1}` |
| **attributes** | Fixed hardware facts | `sample_rate: {value: 5e9, units: Sa/s}` |

### The `when` clause — condition-dependent specs

Datasheet accuracy tables have rows like "100mV range, 24-hour cal, 10 NPLC".
The `specs` array with `when` clauses captures this directly:

```yaml
signals:
  voltage:
    accuracy: {pct_reading: 0.0045}    # default accuracy
    specs:
    - when:                             # on the 100mV range...
        voltage: {min: -0.1, max: 0.1, units: V}
      accuracy: {pct_reading: 0.005}    # ...accuracy is different
    - when:
        nplc: {min: 10, max: 100}       # at high integration time...
      accuracy: {pct_reading: 0.01}     # ...accuracy improves
```

`when` keys reference siblings — a signal, condition, or control defined on
the same capability. Match types: range containment, exact value, list
membership. Units inherit from the referenced parameter.

### What this enables

**"Can this bench test this product?"** becomes a query, not 20 minutes of
mentally cross-referencing datasheets:

```
Product requires: 3.3V ±2% (66mV window)
DMM on bench 3:   34401A on 10V range → accuracy ±0.55mV
→ YES: measurement uncertainty is 100x smaller than the tolerance
```

The catalog has 40+ instruments across 20+ manufacturers. Each entry is
generated from the datasheet PDF using AI-assisted extraction, then
human-reviewed.

---

## 12. Event Sourcing and the Three Stores

Everything that happens during a test is an event. Events are the source of
truth — all other views (Parquet results, CSV exports, UI dashboards) are
derived.

### The event log

```
SessionStarted → RunStarted → StepStarted → MeasurementRecorded → StepEnded → RunEnded
                                    ↑              ↑
                          InstrumentRead    InstrumentSet
```

Every event is a typed Pydantic model with `id`, `occurred_at`, `session_id`,
`run_id`. The `EventLog` stamps `received_at` on emit, buffers events, and
flushes as Arrow IPC batches. One file per session, date-partitioned.

```python
# This happens inside the framework — users don't call it directly
event_log.emit(MeasurementRecorded(
    step_name="test_output_voltage",
    measurement_name="output_voltage",
    value=3.3,
    units="V",
    low_limit=3.234,
    high_limit=3.366,
    outcome="pass",
))
```

**Crash safety:** events are flushed incrementally. GPIB timeout at step 7?
Steps 1-6 are already on disk. No "all or nothing" — partial runs are
preserved.

### Subscribers — different views of the same events

Subscribers watch the event stream and build different outputs. Each
subscriber declares which event types it cares about:

```
EventLog.emit()
    → ParquetSubscriber  (RunStarted, MeasurementRecorded, RunEnded → .parquet)
    → CsvSubscriber      (MeasurementRecorded → .csv)
    → StdfSubscriber     (MeasurementRecorded → .stdf)
    → AtmlSubscriber     (RunStarted, MeasurementRecorded → .xml)
```

Same events, multiple formats, zero coordination between them.

### Channel store — streaming time-series

Separate from the event log. The `ChannelStore` captures high-rate instrument
data — oscilloscope waveforms, DMM readings over time, PSU voltage readback.

```python
# The observer proxy does this automatically on every instrument read
channel_store.write("dmm.voltage", 3.301, units="V")
channel_store.write("dmm.voltage", 3.299, units="V")
channel_store.write("scope.ch1", [3.3, 3.31, 3.29, ...], units="V")
```

Arrow IPC files on disk. Per-channel schemas inferred from first write.
Queryable mid-session (merges flushed files + in-memory buffer). Optional
Arrow Flight server for cross-process streaming to live UI dashboards.

### Parquet results — the queryable measurement store

The `ParquetSubscriber` denormalizes events into wide rows for analytics:

```
MeasurementRecorded event fields    →    Parquet columns
────────────────────────────────         ─────────────────
measurement_name                         measurement_name
value                                    value
units                                    units
low_limit, high_limit                    low_limit, high_limit
outcome                                  outcome
                                    +    dut_serial (from RunStarted)
                                    +    station_id (from RunStarted)
                                    +    step_name (from StepStarted)
                                    +    git_commit (from RunStarted)
```

One Parquet file per run, date-partitioned. DuckDB queries them directly —
the SQL examples in section 8 work because of this structure.

### Three stores, three purposes

| Store | What | Format | Query with |
|-------|------|--------|-----------|
| **EventLog** | Everything that happened | Arrow IPC | Replay, audit |
| **ChannelStore** | High-rate instrument data | Arrow IPC per channel | Arrow Flight, DuckDB |
| **ParquetSubscriber** | Denormalized measurements | Parquet | DuckDB, pandas, Polars |

All files on disk. All open formats. No database server. Centralization is
just file copying — S3 sync, network share, USB stick if you have to.

---

## 13. Instrument Management and Observation

Tests use instruments by role name (`psu`, `dmm`). What happens behind the
scenes: the plugin reads the station YAML, connects each instrument, wraps it
in a transparent proxy, and injects it as a pytest fixture.

### From YAML to fixture — zero boilerplate

```yaml
# stations/demo_station_001.yaml
instruments:
  psu:
    driver: demo.drivers.PSU
    resource: TCPIP::192.168.1.101::INSTR
  dmm:
    driver: demo.drivers.DMM
    resource: TCPIP::192.168.1.102::INSTR
```

```python
# No conftest.py needed. The plugin auto-registers fixtures from station config.
def test_output_voltage(context, logger, psu: PSU, dmm: DMM):
    psu.set_voltage(5.0)
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

`psu` and `dmm` are pytest fixtures created by the Litmus plugin at
collection time. They resolve to connected, proxied instrument instances.

### Transparent observation — every command logged

The proxy wraps the real driver. Every method call, property read, and
attribute set passes through a `DriverObserver` that emits structured events:

```
→ psu.set_voltage(5.0)            # InstrumentSet event
→ psu.enable_output()             # InstrumentConfigure event
→ dmm.measure_dc_voltage()        # InstrumentRead event: value=3.3
```

The test code never sees the proxy — it looks and acts like the real driver.
But every interaction is captured in the event log with timestamps, role,
channel, and value. No print statements. No manual logging. Always on.

The observer is protocol-specific: PyMeasure introspects property descriptors,
VISA parses SCPI strings, QCodes reads Parameter objects. Each driver library
gets its own observer that understands its API conventions.

### Mock instruments — develop without hardware

```yaml
instruments:
  dmm:
    driver: demo.drivers.DMM
    mock: true
    mock_config:
      measure_dc_voltage: 3.3
      measure_resistance: 1000.0
```

`Mock(DMM, measure_dc_voltage=3.3)` creates an object that passes
`isinstance(mock, DMM)` checks. Per-vector mock values let you test edge
cases from your desk:

```yaml
vectors:
- vin: 5.0
mocks:
  dmm.measure_dc_voltage: 3.234   # exactly at the low limit boundary
```

---

## 14. Parallel Multi-DUT Testing

Same test code, multiple boards in parallel. Each slot runs in its own
subprocess. Tests are slot-unaware — the `sync` fixture coordinates when
needed.

### Test code — identical to single-DUT

```python
@pytest.mark.parametrize("vin", [5.0])
@pytest.mark.litmus_limits({...})
def test_output_voltage_synced(vin, logger, psu: PSU, dmm: DMM, sync):
    psu.set_voltage(vin)
    psu.enable_output()

    if sync is not None:               # multi-slot: wait for all boards
        sync.wait("all_powered", timeout=30)

    # each slot measures its own board
    logger.measure("output_voltage", dmm.measure_dc_voltage())
```

`sync` is `None` in single-slot mode — the test runs unchanged. In
multi-slot mode, `sync.wait()` blocks until all slots reach the same point.

### Run two boards in parallel

```bash
pytest tests/test_multi_dut.py \
    --fixture-config=fixtures/dual_power_board.yaml \
    --dut-serials=SN001,SN002 \
    --mock-instruments -v
```

Each subprocess gets its own instrument connections via the fixture config.
File locks handle shared physical resources (e.g., two slots sharing one DMM
through a switch matrix). Results are per-DUT — each board gets its own
Parquet file with its own serial number.

---

## The Mental Model

Three entities, three rates of change, three owners:

| Entity | Changes when... | Changed by... |
|--------|----------------|---------------|
| **Product** | Spec revision | Test engineer |
| **Station** | Swap instruments | Bench technician |
| **Fixture** | Rewire the board | Fixture designer |

Tests reference all three but don't hardcode any of them.
`git diff` shows exactly what changed. PRs enforce review.
