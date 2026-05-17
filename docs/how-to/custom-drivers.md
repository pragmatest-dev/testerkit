# Custom Instrument Drivers

This guide covers creating custom instrument drivers for non-VISA instruments, including simulation patterns for DAQmx, serial devices, and proprietary protocols.

## Architecture Overview

Litmus drivers are plain Python classes you bring yourself — Litmus does not ship driver code. Two base classes live in `src/litmus/instruments/`:

| Base class | Use it for |
|---|---|
| `litmus.instruments.base.Instrument` | Any protocol you handle yourself (serial, DAQmx, USB, HID, proprietary) |
| `litmus.instruments.visa.VisaInstrument` | SCPI / VISA instruments — wraps `pyvisa`, adds `query()` / `write()` and `*IDN?` parsing |

```
Instrument (ABC, in litmus.instruments.base)
   │
   └── VisaInstrument (in litmus.instruments.visa)
            │
            └── Concrete drivers (yours: MyDMM, MyPSU, ...)
```

**Key principles:**
- Your driver class subclasses one base. There are no capability mixins to inherit — what an instrument *can do* is declared in its catalog YAML (`catalog/*.yaml`), not in code.
- The catalog entry is the contract: a station can use any driver whose catalog declares the capabilities the test requires. Drivers are not interchangeable code-side; the matcher works off catalog metadata.
- `sim_config=` (constructor) and `simulate=True` (per-instance) are the universal simulation interface. Real hardware paths and mock paths share one driver class.

## VISA Instruments (Recommended Path)

For SCPI-based instruments, extend `VisaInstrument`:

```python
from litmus.instruments.visa import VisaInstrument

class MyDMM(VisaInstrument):
    """Custom DMM driver."""

    # Define responses for simulation mode
    _sim_responses = {
        "MEAS:VOLT:DC?": "voltage",
        "MEAS:CURR:DC?": "current",
    }

    def measure_voltage(self, signal_type=None) -> float:
        return float(self.query("MEAS:VOLT:DC?"))

    def measure_current(self, signal_type=None) -> float:
        return float(self.query("MEAS:CURR:DC?"))

    # Optional: configure methods
    def configure_voltage_range(self, range_val: float | str) -> None:
        if range_val == "AUTO":
            self.write("VOLT:RANG:AUTO ON")
        else:
            self.write(f"VOLT:RANG {range_val}")

    def configure_current_range(self, range_val: float | str) -> None:
        if range_val == "AUTO":
            self.write("CURR:RANG:AUTO ON")
        else:
            self.write(f"CURR:RANG {range_val}")
```

**Usage:**

```python
# Real hardware
dmm = MyDMM("TCPIP::192.168.1.100::INSTR")

# Simulation (no hardware needed)
dmm = MyDMM(
    "TCPIP::192.168.1.100::INSTR",
    simulate=True,
    sim_config={"voltage": 5.0, "current": 0.1}
)
```

## Non-VISA Instruments

For serial, DAQmx, or proprietary protocols, extend the `Instrument` base class and implement your own simulation.

### Serial Devices

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
        super().__init__(simulate=simulate, sim_config=sim_config)
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None

        # Simulation state
        self._sim_voltage = float(sim_config.get("voltage", 0.0)) if sim_config else 0.0

    def connect(self) -> None:
        if self.simulate:
            self._connected = True
            return
        self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
        self._connected = True

    def disconnect(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None
        self._connected = False

    def _write(self, cmd: str) -> None:
        if self.simulate:
            return
        self._serial.write(f"{cmd}\r\n".encode())

    def _query(self, cmd: str) -> str:
        if self.simulate:
            return self._sim_query(cmd)
        self._serial.write(f"{cmd}\r\n".encode())
        return self._serial.readline().decode().strip()

    def _sim_query(self, cmd: str) -> str:
        """Handle simulated queries."""
        if "VOLT" in cmd:
            return str(self._sim_voltage)
        return "0"

    def measure_voltage(self, signal_type=None) -> float:
        return float(self._query("MEAS:VOLT?"))

    def configure_voltage_range(self, range_val: float | str) -> None:
        self._write(f"VOLT:RANG {range_val}")
```

### NI DAQmx Devices

For DAQmx, create a simulation layer that mimics NI's API:

```python
from typing import Any
from litmus.instruments.base import Instrument

# Import conditionally for systems without DAQmx
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
        physical_channel: str,  # e.g., "Dev1/ai0"
        simulate: bool = False,
        sim_config: dict | None = None,
    ):
        super().__init__(simulate=simulate, sim_config=sim_config)
        self.physical_channel = physical_channel
        self._task: Any = None

        # Simulation state
        self._sim_voltage = float(sim_config.get("voltage", 0.0)) if sim_config else 0.0

    def connect(self) -> None:
        if self.simulate:
            self._connected = True
            return

        if not HAS_DAQMX:
            raise RuntimeError("nidaqmx not installed. Use simulate=True for testing.")

        self._task = nidaqmx.Task()
        self._task.ai_channels.add_ai_voltage_chan(
            self.physical_channel,
            terminal_config=TerminalConfiguration.RSE
        )
        self._connected = True

    def disconnect(self) -> None:
        if self._task:
            self._task.close()
            self._task = None
        self._connected = False

    def measure_voltage(self, signal_type=None) -> float:
        if self.simulate:
            return self._sim_voltage
        return float(self._task.read())

    def configure_voltage_range(self, range_val: float | str) -> None:
        # DAQmx sets range when creating the channel
        # For dynamic range changes, recreate the task
        pass
```

### Proprietary USB/HID Devices

For USB devices with custom protocols:

```python
import struct
from litmus.instruments.base import Instrument

# Conditional import
try:
    import usb.core
    HAS_USB = True
except ImportError:
    HAS_USB = False

class USBPowerSupply(Instrument):
    """USB power supply with proprietary HID protocol."""

    VENDOR_ID = 0x1234
    PRODUCT_ID = 0x5678

    def __init__(
        self,
        simulate: bool = False,
        sim_config: dict | None = None,
    ):
        super().__init__(simulate=simulate, sim_config=sim_config)
        self._device = None

        # Simulation state
        self._sim_voltage = 0.0
        self._sim_enabled = False

    def connect(self) -> None:
        if self.simulate:
            self._connected = True
            return

        if not HAS_USB:
            raise RuntimeError("pyusb not installed. Use simulate=True for testing.")

        self._device = usb.core.find(
            idVendor=self.VENDOR_ID,
            idProduct=self.PRODUCT_ID
        )
        if self._device is None:
            raise RuntimeError("Device not found")
        self._connected = True

    def disconnect(self) -> None:
        self._device = None
        self._connected = False

    def _send_command(self, cmd_id: int, data: bytes) -> bytes:
        if self.simulate:
            return self._sim_command(cmd_id, data)
        # Real USB HID transfer
        packet = struct.pack("<BH", cmd_id, len(data)) + data
        self._device.write(0x01, packet)  # OUT endpoint
        return bytes(self._device.read(0x81, 64))  # IN endpoint

    def _sim_command(self, cmd_id: int, data: bytes) -> bytes:
        """Simulate command responses."""
        if cmd_id == 0x10:  # Set voltage
            voltage = struct.unpack("<f", data)[0]
            self._sim_voltage = float(voltage)
            return b"\x00"  # OK
        elif cmd_id == 0x20:  # Read voltage
            return struct.pack("<f", self._sim_voltage)
        return b"\xFF"  # Error

    def set_voltage(self, voltage: float) -> None:
        data = struct.pack("<f", voltage)
        self._send_command(0x10, data)

    def set_voltage_limit(self, limit: float) -> None:
        # Not supported by this device
        pass

    def enable_output(self, channel: str | None = None) -> None:
        if self.simulate:
            self._sim_enabled = True
            return
        self._send_command(0x30, b"\x01")

    def disable_output(self, channel: str | None = None) -> None:
        if self.simulate:
            self._sim_enabled = False
            return
        self._send_command(0x30, b"\x00")

    def measure_output_voltage(self) -> float:
        if self.simulate:
            return self._sim_voltage if self._sim_enabled else 0.0
        response = self._send_command(0x20, b"")
        return float(struct.unpack("<f", response)[0])
```

## Interface-Level Mocks

For unit testing without any I/O, use interface-level mocks:

```python
from litmus.instruments.mocks import Mock, as_mock
from my_drivers import MyDMM  # your own driver class

# Instant, no I/O overhead - Mock wraps any class
dmm = Mock(MyDMM, measure_voltage=5.0, measure_current=0.1)
dmm.connect()
v = dmm.measure_voltage()  # Returns 5.0

# Dynamic value updates via the mock-control surface
as_mock(dmm).set_mock_value("measure_voltage", 3.3)
v = dmm.measure_voltage()  # Returns 3.3
```

## Creating Custom Mocks

Extend the mock pattern for custom instruments:

```python
from litmus.instruments.base import Instrument

class MockTempLogger(Instrument):
    """Mock temperature logger for testing."""

    def __init__(
        self,
        temperature: float = 25.0,
        **kwargs,
    ):
        # Mocks always simulate
        super().__init__(simulate=True, sim_config={})
        self._values = {"temperature": float(temperature)}
        self._values.update({k: float(v) for k, v in kwargs.items()})

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def set_value(self, name: str, value: float) -> None:
        """Update a simulated value."""
        self._values[name] = float(value)

    def measure_temperature(self, sensor_type: str = "rtd") -> float:
        return self._values["temperature"]
```

## Testing Custom Drivers

Write tests that exercise both simulation modes:

```python
import pytest


class TestMyDMM:
    """Tests for custom DMM driver."""

    def test_measure_voltage_simulated(self):
        """Should work in simulation mode."""
        dmm = MyDMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3}
        )
        dmm.connect()
        v = dmm.measure_voltage()
        assert float(v) == pytest.approx(3.3, abs=0.001)
        dmm.disconnect()

    def test_context_manager(self):
        """Should work as context manager."""
        with MyDMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0}
        ) as dmm:
            assert dmm.measure_voltage() == 5.0

    @pytest.mark.hardware
    def test_measure_voltage_real(self):
        """Test with real hardware (skip in CI)."""
        dmm = MyDMM("TCPIP::192.168.1.100::INSTR")
        dmm.connect()
        v = dmm.measure_voltage()
        assert isinstance(v, float)
        assert v > 0.0  # Sanity check
        dmm.disconnect()
```

## Best Practices

1. **Always implement `simulate=True`** - Tests should run without hardware
2. **Use `_sim_*` methods for simulation logic** - Keep it separate from real I/O
3. **Declare capabilities in the catalog** - `catalog/*.yaml` is the matcher's contract; capability mixins do not live in driver code
4. **Handle missing dependencies gracefully** - Check for optional imports
5. **Use `float` for measurements** - Standard Python numeric type
6. **Provide `sim_config` for test-specific values** - Don't hardcode defaults
7. **Test both modes** - Simulation and (marked) hardware tests

## Registering Custom Drivers

To use custom drivers with the pytest fixtures:

```python
# conftest.py
import pytest
from my_drivers import MyDMM, MyPSU

@pytest.fixture
def dmm(simulate):
    """Custom DMM fixture."""
    with MyDMM(
        "TCPIP::192.168.1.100::INSTR",
        simulate=simulate,
        sim_config={"voltage": 5.0}
    ) as dmm:
        yield dmm
```

Or register via station config:

```yaml
# stations/my_station.yaml
id: my_station
name: "My Test Station"

instruments:
  dmm:
    type: dmm
    driver: my_drivers.MyDMM  # Full module path
    resource: "TCPIP::192.168.1.100::INSTR"
    mock: true
    mock_config:
      voltage: 5.0
```

## Next Steps

- [Capability Interfaces](../concepts/capabilities.md) — Full list of capability protocols
- [Fixture Manager](../concepts/fixtures.md) — Pin-to-instrument routing
- [Litmus fixtures](../reference/litmus-fixtures.md) — `instruments`, `instrument`, `pins`, and the per-role auto-fixtures
