# Writing custom instrument drivers

Litmus doesn't ship instrument drivers — you bring your own. This page covers writing a driver from scratch, choosing between simulation strategies, and registering the driver so the platform finds it.

If your instrument speaks SCPI over VISA, [start with `VisaInstrument`](#scpi-instruments-via-visa). For serial, DAQmx, USB, or proprietary protocols, [extend `Instrument` directly](#non-visa-instruments). For tests that should run without any driver code at all, [use the `Mock` factory](#the-mock-factory).

## Architecture overview

The instrument package (`litmus.instruments.*`) gives you two base classes and one factory:

| Surface | Import | Use it for |
|---|---|---|
| `Instrument` | `from litmus.instruments.base import Instrument` | Any protocol you handle yourself — serial, DAQmx, USB, HID, proprietary RPC |
| `VisaInstrument` | `from litmus.instruments.visa import VisaInstrument` | SCPI / IEEE 488.2 instruments — wraps PyVISA, adds `query()` / `write()` / `*IDN?` parsing, generates a `pyvisa-sim` config when `simulate=True` |
| `Mock` (factory function) | `from litmus.instruments.mocks import Mock` | Tests that should bypass driver code entirely. Wraps any class and returns an instance whose methods are no-ops unless explicitly configured |

The package's `__init__.py` is documentation-only — import from the submodules directly. `from litmus.instruments import Instrument` does not work.

```
Instrument (ABC, in base.py)
   │
   ├── VisaInstrument (in visa.py) — SCPI + pyvisa-sim
   │      └── your concrete VISA drivers (MyDMM, MyPSU, ...)
   └── your direct subclasses (SerialDMM, DaqmxAI, USBPowerSupply, ...)

Mock (factory in mocks.py) — orthogonal to the class hierarchy
   └── Mock(AnyClass, **return_values) → mock instance of AnyClass
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

## The `Mock` factory

`Mock()` is orthogonal to the `simulate=True` driver-internal flag. It wraps **any** class and returns an instance whose methods are no-ops unless you give them return values. The platform uses this when station YAML says `mock: true` (or `--mock-instruments` is passed) — see [Mock mode vs `simulate=True`](#mock-mode-vs-simulatetrue) below.

```python
from litmus.instruments.mocks import Mock, as_mock

# Simple scalar values
dmm = Mock(MyDMM, measure_voltage=5.0, measure_current=0.1)
dmm.measure_voltage()         # 5.0
dmm.measure_current()         # 0.1
dmm.set_voltage(3.3)          # no-op, returns None

# Dict values for argument-based lookup — great for SCPI
inst = Mock(MyDMM, query={
    "MEAS:VOLT?": "5.0",
    "*IDN?": "Vendor,Model,SN1,1.0",
})
inst.query("MEAS:VOLT?")      # "5.0"

# Callable values for full control
inst = Mock(MyDMM, query=lambda cmd: "5.0" if "VOLT" in cmd else "0.0")

# Dynamic value updates via the mock-control surface
as_mock(dmm).set_mock_value("measure_voltage", 3.3)
dmm.measure_voltage()         # 3.3
```

`Mock` preserves the typed surface — `dmm` is typed as `MyDMM`, so editor autocomplete and type checkers see the real driver's methods. Use `as_mock(instance)` to reach the mock-specific control surface (`set_mock_value`, `mock_values`, `_connected`) without fighting the declared type.

`Mock` instances behave as context managers (`connect()` → `disconnect()` are wired automatically) and pass `isinstance(mock, MyDMM)` checks.

---

## Mock mode vs `simulate=True`

The platform has **two** independent mock paths. Knowing which one fires when matters for writing useful tests.

| Path | What it does | How it's triggered |
|---|---|---|
| Driver-internal `simulate=True` | Your driver's own simulation branch runs. For VISA: pyvisa-sim. For non-VISA: your hand-written `if self.simulate: ...` branches. The driver class is instantiated normally; identity fields populate from your code. | You pass `simulate=True` when constructing the driver yourself. |
| Platform mock mode | The driver class is NOT instantiated. The platform calls `Mock(object, **mock_config)` and substitutes the mock for the real driver. Your `simulate=True` branch never runs. | `mock: true` on the instrument block in station YAML; OR `--mock-instruments` CLI flag; OR `LITMUS_MOCK_INSTRUMENTS=1` environment variable. |

When platform mock mode is active, the station YAML's `mock_config:` keys become method names whose return values the Mock will produce. The driver's own `simulate=True` branch is dead code in this path.

```yaml
# stations/my_station.yaml
instruments:
  dmm:
    type: dmm
    driver: my_pkg.MyDMM
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_voltage: 5.0     # Mock(MyDMM).measure_voltage() → 5.0
      measure_current: 0.1
```

With `pytest --mock-instruments` (or `mock: true` on the instrument), the test gets a `Mock(MyDMM, measure_voltage=5.0, measure_current=0.1)` — `MyDMM.__init__` is never called.

If you specifically want your driver's `simulate=True` branch to exercise (because you've written non-trivial simulation logic in it), construct the driver yourself in a fixture rather than relying on platform mock mode.

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

### conftest.py (local override)

For ad-hoc tests where you don't want a station YAML, define the fixture yourself in `conftest.py`:

```python
# conftest.py
import pytest

from litmus.client import Mock
from my_pkg.drivers import MyDMM


@pytest.fixture
def dmm(mock_instruments):
    """Custom DMM fixture. `mock_instruments` is True when
    --mock-instruments or LITMUS_MOCK_INSTRUMENTS=1 is set."""
    if mock_instruments:
        yield Mock(MyDMM, measure_voltage=5.0)
        return

    with MyDMM("TCPIP::192.168.1.100::INSTR") as inst:
        yield inst
```

Use the station-YAML path for production benches (it's what `litmus serve` / the operator UI / capability matching all read). Use conftest fixtures for ad-hoc tests or to override a station-defined instrument with custom setup.

---

## Testing your driver

Test the driver's own behavior — its `simulate=True` branches, its `connect()` / `disconnect()` lifecycle, its method outputs — directly. This is independent of how the platform wires it up.

```python
import pytest

from my_pkg.drivers import MyDMM


class TestMyDMM:
    """Driver-level tests; no Litmus plugin needed."""

    def test_measure_voltage_simulated(self):
        dmm = MyDMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        )
        dmm.connect()
        try:
            assert dmm.measure_voltage() == pytest.approx(3.3, abs=0.001)
        finally:
            dmm.disconnect()

    def test_context_manager(self):
        with MyDMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0},
        ) as dmm:
            assert dmm.measure_voltage() == 5.0
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
- **Don't conflate `simulate=True` with `Mock`.** Driver-internal `simulate=True` is for "this driver simulates itself"; platform mock mode (`Mock(object, **mock_config)`) bypasses your driver entirely. Document which mode your fixture uses.
- **Capabilities live in the catalog, not in code.** Your driver class is just code — Litmus learns "this is a DMM that measures DC voltage" from the catalog YAML you point `catalog_ref:` at. Don't try to declare capabilities via Python mixins or class attributes.

## See also

- [Catalog schema](../reference/catalog-schema.md) — what a `catalog/<vendor>/<model>.yaml` entry declares (the matcher's contract)
- [Catalog cookbook](../reference/catalog-cookbook.md) — worked recipes for common datasheet shapes
- [Capabilities](../concepts/capabilities.md) — capability model + matching algorithm
- [Configuring stations](configuring-stations.md) — the `driver:` field and the rest of the station YAML
- [Mock mode](mock-mode.md) — `--mock-instruments`, `mock_config:`, the three mock pipelines
- [Litmus fixtures](../reference/litmus-fixtures.md) — `instruments`, `instrument`, `pins`, `mock_instruments`, and how per-role auto-fixtures get registered
