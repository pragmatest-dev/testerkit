# Custom Instrument Drivers

This guide covers creating custom instrument drivers for non-VISA instruments, including simulation patterns for DAQmx, serial devices, and proprietary protocols.

## Architecture Overview

Litmus instruments follow a layered architecture:

```
Capability Interfaces (VoltageInput, VoltageOutput, ...)
        │
        ▼
Protocol Base Classes (VisaInstrument, SerialInstrument, ...)
        │
        ▼
Concrete Drivers (DMM, PSU, YourCustomInstrument)
```

**Key principles:**
- Capabilities are interchangeable (any `VoltageInput` can replace another)
- Drivers are NOT interchangeable (embrace vendor weirdness)
- `simulate=True` enables mock mode for driver-level testing

## VISA Instruments (Recommended Path)

For SCPI-based instruments, extend `VisaInstrument`:

```python
from litmus.instruments.visa import VisaInstrument
from litmus.capabilities.interfaces import VoltageInput, CurrentInput

class MyDMM(VisaInstrument, VoltageInput, CurrentInput):
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
    mock_config={"voltage": 5.0, "current": 0.1}
)
```

## Non-VISA Instruments

For serial, DAQmx, or proprietary protocols, extend the `Instrument` base class and implement your own simulation.

### Serial Devices

```python
import serial
from litmus.instruments.base import Instrument
from litmus.capabilities.interfaces import VoltageInput

class SerialDMM(Instrument, VoltageInput):
    """DMM with serial (RS-232) interface."""

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        simulate: bool = False,
        mock_config: dict | None = None,
    ):
        super().__init__(simulate=simulate, mock_config=mock_config)
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None

        # Simulation state
        self._sim_voltage = float(mock_config.get("voltage", 0.0))) if mock_config else 0.0

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
        self._ensure_connected()
        return float(self._query("MEAS:VOLT?"))

    def configure_voltage_range(self, range_val: float | str) -> None:
        self._ensure_connected()
        self._write(f"VOLT:RANG {range_val}")
```

### NI DAQmx Devices

For DAQmx, create a simulation layer that mimics NI's API:

```python
from typing import Any
from litmus.instruments.base import Instrument
from litmus.capabilities.interfaces import VoltageInput

# Import conditionally for systems without DAQmx
try:
    import nidaqmx
    from nidaqmx.constants import TerminalConfiguration
    HAS_DAQMX = True
except ImportError:
    HAS_DAQMX = False

class DaqmxAnalogInput(Instrument, VoltageInput):
    """NI DAQmx analog input channel."""

    def __init__(
        self,
        physical_channel: str,  # e.g., "Dev1/ai0"
        simulate: bool = False,
        mock_config: dict | None = None,
    ):
        super().__init__(simulate=simulate, mock_config=mock_config)
        self.physical_channel = physical_channel
        self._task: Any = None

        # Simulation state
        self._sim_voltage = float(mock_config.get("voltage", 0.0))) if mock_config else 0.0

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
        self._ensure_connected()
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
from litmus.capabilities.interfaces import VoltageOutput

# Conditional import
try:
    import usb.core
    HAS_USB = True
except ImportError:
    HAS_USB = False

class USBPowerSupply(Instrument, VoltageOutput):
    """USB power supply with proprietary HID protocol."""

    VENDOR_ID = 0x1234
    PRODUCT_ID = 0x5678

    def __init__(
        self,
        simulate: bool = False,
        mock_config: dict | None = None,
    ):
        super().__init__(simulate=simulate, mock_config=mock_config)
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
            return struct.pack("<f", float(self._sim_voltage))
        return b"\xFF"  # Error

    def set_voltage(self, voltage: float) -> None:
        self._ensure_connected()
        data = struct.pack("<f", float(voltage))
        self._send_command(0x10, data)

    def set_voltage_limit(self, limit: float) -> None:
        # Not supported by this device
        pass

    def enable_output(self, channel: str | None = None) -> None:
        self._ensure_connected()
        if self.simulate:
            self._sim_enabled = True
            return
        self._send_command(0x30, b"\x01")

    def disable_output(self, channel: str | None = None) -> None:
        self._ensure_connected()
        if self.simulate:
            self._sim_enabled = False
            return
        self._send_command(0x30, b"\x00")

    def measure_output_voltage(self) -> float:
        self._ensure_connected()
        if self.simulate:
            return self._sim_voltage if self._sim_enabled else 0.0
        response = self._send_command(0x20, b"")
        return float(struct.unpack("<f", response)[0])
```

## Mock Mode in Tests

For running tests without hardware, use `--mock-instruments` with config-driven mock values:

```yaml
# stations/my_station.yaml
instruments:
  dmm:
    type: my_drivers.MyDMM
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 5.0
      current: 0.1
```

```bash
pytest tests/ --station-config=stations/my_station.yaml --mock-instruments
```

See [Mock Mode](simulation-mode.md) for per-test and per-vector mock configuration.

## Creating Custom Mocks

Extend the mock pattern for custom instruments:

```python
from litmus.instruments.base import Instrument
from litmus.capabilities.interfaces import TemperatureInput

class MockTempLogger(Instrument, TemperatureInput):
    """Mock temperature logger for testing."""

    def __init__(
        self,
        temperature: float = 25.0,
        **kwargs,
    ):
        # Mocks always simulate
        super().__init__(simulate=True, mock_config={})
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
            mock_config={"voltage": 3.3}
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
            mock_config={"voltage": 5.0}
        ) as dmm:
            assert dmm.measure_voltage() == pytest.approx(5.0)

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
3. **Implement capability interfaces** - Enable protocol-based testing
4. **Handle missing dependencies gracefully** - Check for optional imports
5. **Use `float` for measurements** - Standard Python floats with 15-17 significant digits
6. **Provide `mock_config` for test-specific values** - Don't hardcode defaults
7. **Test both modes** - Simulation and (marked) hardware tests

## Registering Custom Drivers

Register drivers via station config. The Litmus plugin auto-registers a pytest fixture for each role -- no conftest boilerplate needed:

```yaml
# stations/my_station.yaml
station:
  id: my_station
  name: "My Test Station"

instruments:
  dmm:
    driver: my_drivers.MyDMM  # Full import path
    resource: "TCPIP::192.168.1.100::INSTR"
    simulate: true
    mock_config:
      voltage: 5.0
```

Tests can use the role name directly:

```python
def test_voltage(dmm):
    """dmm is auto-registered from station config."""
    assert dmm.measure_voltage() > 3.0
```

To override the auto-registered fixture with custom lifecycle logic, define it in `conftest.py`:

```python
# conftest.py
import pytest

@pytest.fixture(scope="session")
def dmm(instruments):
    """Custom DMM with additional setup."""
    inst = instruments["dmm"]
    inst.configure_voltage_range("AUTO")
    return inst
```

## Next Steps

- [Capability Interfaces](../capabilities.md) — Full list of capability protocols
- [Fixture Manager](../fixtures.md) — Pin-to-instrument routing
- [pytest Plugin](../pytest-plugin.md) — Using instruments in tests
