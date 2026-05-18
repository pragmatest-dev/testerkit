# Writing custom instrument drivers

Litmus doesn't ship instrument drivers — you bring your own. This page covers writing a driver, registering it so the platform finds it, and the two hardware-free paths Litmus supports.

If your instrument speaks SCPI over VISA, [start with `VisaInstrument`](#scpi-instruments-via-visa). For serial, DAQmx, USB, or proprietary protocols, [extend `Instrument` directly](#non-visa-instruments).

## Architecture overview

The instrument package (`litmus.instruments.*`) gives you two base classes and one mock factory:

| Surface | Import | Use it for |
|---|---|---|
| `Instrument` | `from litmus.instruments.base import Instrument` | Any protocol you handle yourself — serial, DAQmx, USB, HID, proprietary RPC |
| `VisaInstrument` | `from litmus.instruments.visa import VisaInstrument` | SCPI / IEEE 488.2 instruments — wraps PyVISA, adds `query()` / `write()` / `*IDN?` parsing, generates a `pyvisa-sim` config when `simulate=True` |
| `Mock` | `from litmus.instruments.mocks import Mock` | Substitute for a driver class in tests. Returns a `class MockClass(cls)` instance so `isinstance(mock, MyDMM)` passes, `connect()`/`disconnect()` are auto-wired no-ops, and only explicitly-configured methods return values. The platform calls this for you from station YAML's `mock_config:`; you import it directly only for bringup-tier conftest fixtures. |

The package's `__init__.py` is documentation-only — import from the submodules directly. `from litmus.instruments import Instrument` does not work.

```
Instrument (ABC, in base.py)
   │
   ├── VisaInstrument (in visa.py) — SCPI + pyvisa-sim
   │      └── your concrete VISA drivers (MyDMM, MyPSU, ...)
   └── your direct subclasses (SerialDMM, DaqmxAI, USBPowerSupply, ...)

Mock (mocks.py) — orthogonal to the class hierarchy
   └── Mock(AnyClass, **method_values) → instance of a subclass of AnyClass
```

## What an instrument advertises to the platform

A driver class is just Python — it doesn't declare capabilities in code. The capability metadata that the matcher uses ("this is a DMM that measures DC voltage") lives in the [catalog YAML](../reference/catalog-schema.md), referenced from the station YAML's `instruments:` block. Two pieces wire your driver into the platform:

1. **Station YAML** — `instruments: { dmm: { driver: my_pkg.MyDMM, catalog_ref: my_pkg.my_dmm, resource: ... } }`. The `driver:` path is what Python imports; the dictionary key `dmm:` is the [per-role auto-fixture](../reference/litmus-fixtures.md#per-role-auto-fixtures) name tests see.
2. **Catalog YAML** — declares the function / direction / signals the matcher pairs against product characteristics. See [catalog schema](../reference/catalog-schema.md) and the [catalog cookbook](../reference/catalog-cookbook.md) for the YAML shape.

The driver's class is what gets called; the catalog is what gets matched. They're independent.

---

## SCPI instruments via VISA

For any SCPI / IEEE 488.2 instrument, subclass `VisaInstrument`. The base handles `connect()` / `disconnect()`, `write()` / `query()` / `read()`, `*IDN?` parsing, and generates a pyvisa-sim YAML when `simulate=True`.

```python
from litmus.instruments.visa import VisaInstrument


class MyDMM(VisaInstrument):
    """Custom SCPI DMM driver."""

    def measure_voltage(self) -> float:
        # Use MEAS:VOLT? (not MEAS:VOLT:DC?) — the auto-generated
        # pyvisa-sim YAML wires `voltage` in sim_config to MEAS:VOLT?.
        return float(self.query("MEAS:VOLT?"))

    def measure_current(self) -> float:
        return float(self.query("MEAS:CURR?"))

    def configure_voltage_range(self, range_val: float | str) -> None:
        if range_val == "AUTO":
            self.write("VOLT:RANG:AUTO ON")
        else:
            self.write(f"VOLT:RANG {range_val}")
```

Usage on real hardware:

```python
dmm = MyDMM("TCPIP::192.168.1.100::INSTR")
dmm.connect()
v = dmm.measure_voltage()
dmm.disconnect()
```

In `simulate=True` mode, the base class generates a pyvisa-sim YAML on `connect()`:

```python
dmm = MyDMM(
    "TCPIP::192.168.1.100::INSTR",
    simulate=True,
    sim_config={"voltage": 5.0, "current": 0.1},
)
dmm.connect()
v = dmm.measure_voltage()   # 5.0
```

### What `sim_config` controls

The auto-generated pyvisa-sim YAML wires `voltage` and `current` from `sim_config` into stateful properties matching `MEAS:VOLT?` and `MEAS:CURR?` queries respectively. Setters (`VOLT {value}`, `CURR {value}`) update that state. You also get:

| sim_config key | Effect |
|---|---|
| `voltage: <float>` | Default value returned by `MEAS:VOLT?`; `VOLT <value>` updates it |
| `current: <float>` | Default value returned by `MEAS:CURR?`; `CURR <value>` updates it |
| `idn: "Vendor,Model,Serial,Firmware"` | Overrides the `*IDN?` response (default: `Litmus,SimulatedVisa,SN001,1.0`) |
| `responses: {<scpi-cmd>: <response>}` | Static query-response dialogues for any other SCPI command. The response is returned as a literal string. |
| `noise: {<name>: <pct>}` | Used by `_get_sim_value(name)` helper to add random noise. Useful if your driver implements `simulate` branches by hand. |

If your driver uses non-standard SCPI commands (e.g. `MEAS:VOLT:DC?` instead of `MEAS:VOLT?`), add them via `responses`:

```python
dmm = MyDMM(
    "TCPIP::192.168.1.100::INSTR",
    simulate=True,
    sim_config={"responses": {"MEAS:VOLT:DC?": "5.0"}},
)
```

The response value is the literal string returned to the query. `float("5.0")` works; `float("voltage")` does not. Don't put placeholder names like `"voltage"` in the response — put the actual value.

### Class-level `_sim_responses`

If your driver always needs a particular static SCPI dialogue (e.g. a system query), set it on the class so every instance gets it:

```python
class MyDMM(VisaInstrument):
    _sim_responses = {
        "SYST:ERR?": '0,"No error"',
        "CAL:STAT?": "OK",
    }
```

Per-instance `sim_config["responses"]` merges over `_sim_responses` (instance wins).

---

## Non-VISA instruments

For serial, DAQmx, USB, or any other protocol, subclass `Instrument` and own `connect()` / `disconnect()` plus simulation yourself. The base class gives you `resource`, `simulate`, `sim_config`, `_connected`, four optional identity fields (`manufacturer`, `model`, `serial`, `firmware`), and the context-manager protocol.

### Pattern: serial device

```python
import serial

from litmus.instruments.base import Instrument


class SerialDMM(Instrument):
    """DMM with serial (RS-232) interface."""

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        simulate: bool = False,
        sim_config: dict | None = None,
    ):
        # Pass the port as `resource` so the base records it for traceability.
        super().__init__(resource=port, simulate=simulate, sim_config=sim_config)
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None
        self._sim_voltage = float((sim_config or {}).get("voltage", 0.0))

    def connect(self) -> None:
        if self.simulate:
            self._connected = True
            return
        self._serial = serial.Serial(self.resource, self.baudrate, timeout=1)
        self._connected = True

    def disconnect(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None
        self._connected = False

    def measure_voltage(self) -> float:
        if self.simulate:
            return self._sim_voltage
        self._serial.write(b"MEAS:VOLT?\r\n")
        return float(self._serial.readline().decode().strip())
```

### Pattern: NI DAQmx

Guard the import so the driver imports on machines without DAQmx installed:

```python
from typing import Any

from litmus.instruments.base import Instrument

try:
    import nidaqmx
    from nidaqmx.constants import TerminalConfiguration

    HAS_DAQMX = True
except ImportError:
    HAS_DAQMX = False


class DaqmxAnalogInput(Instrument):
    """NI DAQmx analog input channel."""

    def __init__(
        self,
        physical_channel: str,         # e.g. "Dev1/ai0"
        simulate: bool = False,
        sim_config: dict | None = None,
    ):
        super().__init__(resource=physical_channel, simulate=simulate, sim_config=sim_config)
        self._task: Any = None
        self._sim_voltage = float((sim_config or {}).get("voltage", 0.0))

    def connect(self) -> None:
        if self.simulate:
            self._connected = True
            return
        if not HAS_DAQMX:
            raise RuntimeError(
                "nidaqmx is not installed; pass simulate=True or run under "
                "--mock-instruments for hardware-free tests."
            )
        self._task = nidaqmx.Task()
        self._task.ai_channels.add_ai_voltage_chan(
            self.resource, terminal_config=TerminalConfiguration.RSE
        )
        self._connected = True

    def disconnect(self) -> None:
        if self._task:
            self._task.close()
            self._task = None
        self._connected = False

    def measure_voltage(self) -> float:
        if self.simulate:
            return self._sim_voltage
        return float(self._task.read())
```

### Pattern: proprietary USB / HID

```python
import struct

from litmus.instruments.base import Instrument

try:
    import usb.core

    HAS_USB = True
except ImportError:
    HAS_USB = False


class USBPowerSupply(Instrument):
    """Power supply with a proprietary HID protocol."""

    VENDOR_ID = 0x1234
    PRODUCT_ID = 0x5678

    def __init__(self, simulate: bool = False, sim_config: dict | None = None):
        super().__init__(
            resource=f"USB:{self.VENDOR_ID:04x}:{self.PRODUCT_ID:04x}",
            simulate=simulate,
            sim_config=sim_config,
        )
        self._device = None
        self._sim_voltage = 0.0
        self._sim_enabled = False

    def connect(self) -> None:
        if self.simulate:
            self._connected = True
            return
        if not HAS_USB:
            raise RuntimeError("pyusb not installed; pass simulate=True for tests.")
        self._device = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        if self._device is None:
            raise RuntimeError("Device not found")
        self._connected = True

    def disconnect(self) -> None:
        self._device = None
        self._connected = False

    def set_voltage(self, voltage: float) -> None:
        if self.simulate:
            self._sim_voltage = float(voltage)
            return
        packet = struct.pack("<BHf", 0x10, 4, voltage)
        self._device.write(0x01, packet)

    def measure_output_voltage(self) -> float:
        if self.simulate:
            return self._sim_voltage if self._sim_enabled else 0.0
        self._device.write(0x01, struct.pack("<BH", 0x20, 0))
        return float(struct.unpack("<f", bytes(self._device.read(0x81, 64))[:4])[0])
```

### Identity fields on non-VISA drivers

`VisaInstrument` populates `manufacturer` / `model` / `serial` / `firmware` from `*IDN?` automatically. Non-VISA drivers either leave them `None` (no identity verification) or populate them in `connect()`:

```python
def connect(self) -> None:
    # ... open the connection ...
    self.manufacturer = "MyVendor"
    self.model = "MyDMM-1000"
    self.serial = self._read_serial_from_device()
    self.firmware = self._read_firmware_version()
```

---

## Running without hardware

Two paths. They behave differently and you pick based on what the test is exercising.

### Platform mock-mode — the default

In station YAML, set `mock: true` and list the method return values under `mock_config:`. The platform substitutes a stand-in for the real driver: your driver class is never instantiated, `connect()` is never called, and methods you listed return the configured values (everything else returns `None`).

```yaml
# stations/my_station.yaml
instruments:
  dmm:
    type: dmm
    driver: my_pkg.MyDMM
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true                       # opt in at the instrument level
    mock_config:
      measure_voltage: 5.0           # dmm.measure_voltage() → 5.0 inside tests
      measure_current: 0.1
      query:                         # dict — first arg is the lookup key
        "MEAS:VOLT?": "5.0"
        "*IDN?": "Vendor,Model,SN1,1.0"
```

`mock_config:` keys are **method names on your driver**, not signal names. Values can be:

- a scalar → returned on every call regardless of args
- a dict → first positional argument is the lookup key (great for SCPI `query()`)
- a callable → invoked with the call's args, return value goes back to the test

Method names you don't list still exist (they're on the class) and become no-ops returning `None`. Attribute names that don't exist on the class raise `AttributeError` — that's the seam where a typo in `mock_config:` keys shows up.

`--mock-instruments` (CLI) and `LITMUS_MOCK_INSTRUMENTS=1` (env var) force `mock: true` for every instrument in the station. Test code is identical whether the station is real or mocked:

```python
def test_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_voltage())
```

`dmm` resolves to a real `MyDMM` against hardware and to a `MockMyDMM` returning `5.0` against `mock: true` — pytest never sees the difference. The auto-fixture is registered from the station YAML's `instruments:` keys; see [Litmus fixtures](../reference/litmus-fixtures.md#per-role-auto-fixtures).

For the full mock-mode surface (sidecar `mocks:` overrides, the three layered pipelines, resolution order) see [mock-mode.md](mock-mode.md).

### Driver-internal `simulate=True` — when you write the simulation yourself

`Instrument` takes a `simulate: bool` flag on `__init__` and stores it. **What that flag actually does is up to your driver.** The base class does nothing with it; the platform doesn't wire it. If you write `if self.simulate: ...` branches in your methods, those branches run. If you don't, `simulate=True` is silent.

The exception is `VisaInstrument`, which auto-generates a pyvisa-sim YAML on `connect()`. The generator (`src/litmus/instruments/visa.py:177-273`) wires exactly two SCPI properties: `voltage` (queries `MEAS:VOLT?`, setter `VOLT {value}`) and `current` (queries `MEAS:CURR?`, setter `CURR {value}`), plus `*IDN?` and whatever static dialogues you list in `sim_config["responses"]`. That covers a DMM measuring DC voltage / current. Resistance, frequency, scope waveforms, PSU output-enable state, anything else — your driver writes its own `if self.simulate:` branches or its own SCPI dialogue entries.

For non-VISA protocols, there is no framework simulation. `Instrument.__init__` stores `simulate=True`; the rest is your code. The DAQmx and serial examples below show the pattern.

Use `simulate=True` when you've put real work into the driver's own simulation logic — a pyvisa-sim setup that holds state, a state machine for a sequencer, a closed-loop model for a PSU — and the test needs to exercise that logic. Otherwise use platform mock-mode (`mock_config:`); it doesn't require any simulation code in the driver at all.

---

## Registering your driver

### Station YAML (production path)

The canonical way: name the driver via dotted import path in your station YAML. The platform imports it via `importlib`, instantiates with `driver_class(resource)`, and registers a fixture under the dictionary key:

```yaml
# stations/my_station.yaml
id: my_station
name: "My Test Station"

instruments:
  dmm:                              # ← becomes the `dmm` pytest fixture
    type: dmm
    driver: my_pkg.drivers.MyDMM    # ← importlib.import_module + getattr
    catalog_ref: my_pkg.my_dmm      # ← what the matcher reads
    resource: "TCPIP::192.168.1.100::INSTR"
```

Now `def test_voltage(dmm, verify): ...` resolves `dmm` to a connected `MyDMM` instance.

For [station configuration](configuring-stations.md) details (other `driver:` examples, multi-channel routing, the `catalog_ref:` link) see the how-to. For the `catalog_ref:` target schema see [catalog schema](../reference/catalog-schema.md).

### conftest.py (bringup tier — no station YAML yet)

Before you have a station YAML, write the fixture yourself in `conftest.py`. The Litmus-provided `mock_instruments` fixture is `True` when `--mock-instruments` or `LITMUS_MOCK_INSTRUMENTS=1` is set, so the same fixture serves real and mock paths:

```python
# tests/conftest.py
import pytest

from litmus.instruments.mocks import Mock
from my_pkg.drivers import MyDMM


@pytest.fixture(scope="session")
def dmm(mock_instruments) -> MyDMM:
    if mock_instruments:
        return Mock(MyDMM, measure_voltage=5.0, measure_current=0.1)
    return MyDMM("TCPIP::192.168.1.100::INSTR")
```

This is the same pattern [tutorial step 2](../tutorial/02-mock-instruments.md) introduces — `Mock(MyDMM, **values)` returns a `MockMyDMM` instance whose declared methods become no-ops returning your configured values. `isinstance(dmm, MyDMM)` still passes; `dmm.set_voltage(3.3)` is a silent no-op; `dmm.measure_voltage()` returns `5.0`.

Step up to station YAML once you have more than one bench or want capability matching. The station path supersedes the conftest fixture — the platform auto-registers a `dmm` fixture from `instruments.dmm:` in the YAML.

---

## Testing your driver

Two scopes: testing *the driver class itself* (does its `connect()` open the port? does its `measure_voltage()` parse the response correctly?) is separate from testing *a procedure that uses the driver* (which is what `mock_config:` + the Litmus plugin handle).

For driver-level tests, exercise whatever simulation pathway you've actually built. If your driver is `VisaInstrument`-based and you only need voltage/current/IDN, the auto-generated pyvisa-sim config covers you:

```python
import pytest

from my_pkg.drivers import MyDMM


def test_measure_voltage_simulated():
    with MyDMM(
        "TCPIP::192.168.1.100::INSTR",
        simulate=True,
        sim_config={"voltage": 3.3},
    ) as dmm:
        assert dmm.measure_voltage() == pytest.approx(3.3, abs=0.001)
```

If your driver needs methods the auto-sim doesn't cover (resistance, frequency, waveform, output-enable state, anything non-voltage / non-current), add static SCPI dialogues via `sim_config["responses"]` for query-shaped methods, or write `if self.simulate:` branches inside the driver. There is no auto-simulation for non-VISA drivers — `Instrument.__init__` stores the `simulate` flag and that's it.

When in doubt for driver-level tests, use `Mock` directly — it doesn't depend on any simulation infrastructure being present:

```python
from litmus.instruments.mocks import Mock

from my_pkg.drivers import MyDMM


def test_overrange_handling():
    dmm = Mock(MyDMM, measure_voltage=999.0)
    # exercise your driver-wrapper code that consumes measure_voltage()
```

To gate tests on real hardware availability, register a marker in your project's `conftest.py` and use `-m` to filter:

```python
# conftest.py
def pytest_configure(config):
    config.addinivalue_line("markers", "hardware: requires real instruments")
```

```python
@pytest.mark.hardware
def test_measure_voltage_real():
    with MyDMM("TCPIP::192.168.1.100::INSTR") as dmm:
        v = dmm.measure_voltage()
        assert isinstance(v, float) and v > 0.0
```

Then:

```bash
pytest -m hardware                  # only hardware tests
pytest -m "not hardware"            # only simulation tests (CI default)
```

`hardware` is not a Litmus-registered marker; the seven `litmus_*` markers are listed in [`litmus-markers.md`](../reference/litmus-markers.md). You own this marker locally.

---

## Best practices

- **Pass `resource=` to the base.** Non-VISA drivers should pass the connection identifier (port, channel, vendor:product) to `super().__init__(resource=...)` so traceability and the operator UI display something meaningful.
- **Guard optional dependencies.** Wrap `import nidaqmx` / `import usb.core` / etc. in a `try / except ImportError` block so the driver imports cleanly on hardware-free hosts.
- **Let the station YAML decide mock vs real.** Don't import any mock class in your driver or test code. `mock: true` + `mock_config:` in the station block is the canonical path; the platform substitutes a stand-in that returns your listed method values. Reach for driver-internal `simulate=True` only when you've written non-trivial simulation logic that the test should exercise.
- **Capabilities live in the catalog, not in code.** Your driver class is just code — Litmus learns "this is a DMM that measures DC voltage" from the catalog YAML you point `catalog_ref:` at. Don't try to declare capabilities via Python mixins or class attributes.

## See also

- [Catalog schema](../reference/catalog-schema.md) — what a `catalog/<vendor>/<model>.yaml` entry declares (the matcher's contract)
- [Catalog cookbook](../reference/catalog-cookbook.md) — worked recipes for common datasheet shapes
- [Capabilities](../concepts/capabilities.md) — capability model + matching algorithm
- [Configuring stations](configuring-stations.md) — the `driver:` field and the rest of the station YAML
- [Mock mode](mock-mode.md) — `--mock-instruments`, `mock_config:`, the three mock pipelines
- [Litmus fixtures](../reference/litmus-fixtures.md) — `instruments`, `instrument`, `pins`, `mock_instruments`, and how per-role auto-fixtures get registered
