"""Capability interfaces for instrument abstraction.

These Protocol classes define the functional capabilities that instruments
can implement. They are inspired by IVI Foundation class specifications
but don't require IVI drivers.

Key design principles:
- Capabilities are interchangeable (any VoltageInput can replace another)
- Drivers are NOT interchangeable (embrace vendor weirdness)
- Protocol classes enable duck typing and static type checking

Usage in tests:
    def test_voltage(voltage_meter: VoltageInput):
        v = voltage_meter.measure_voltage()
        assert v > Decimal("3.0")

Usage in drivers:
    class DMM(VisaInstrument, VoltageInput, CurrentInput, ResistanceInput):
        def measure_voltage(self, signal_type=SignalType.DC) -> Decimal:
            return Decimal(self.query("MEAS:VOLT:DC?"))
"""

from decimal import Decimal
from typing import Protocol, runtime_checkable

from litmus.capabilities.models import SignalType

# =============================================================================
# Measurement Capabilities (INPUT direction)
# =============================================================================


@runtime_checkable
class VoltageInput(Protocol):
    """Measure DC/AC voltage (IVI-DMM inspired).

    Implemented by: DMM, Oscilloscope, SMU, DAQ analog input
    """

    def measure_voltage(self, signal_type: SignalType = SignalType.DC) -> Decimal:
        """Measure voltage and return the reading.

        Args:
            signal_type: DC or AC measurement mode

        Returns:
            Measured voltage in the configured units (typically Volts)
        """
        ...

    def configure_voltage_range(self, range_val: Decimal | str) -> None:
        """Configure the voltage measurement range.

        Args:
            range_val: Range value in Volts, or "AUTO" for autoranging
        """
        ...


@runtime_checkable
class CurrentInput(Protocol):
    """Measure DC/AC current (IVI-DMM inspired).

    Implemented by: DMM, SMU, Current probe, Shunt-based measurement
    """

    def measure_current(self, signal_type: SignalType = SignalType.DC) -> Decimal:
        """Measure current and return the reading.

        Args:
            signal_type: DC or AC measurement mode

        Returns:
            Measured current in Amps
        """
        ...

    def configure_current_range(self, range_val: Decimal | str) -> None:
        """Configure the current measurement range.

        Args:
            range_val: Range value in Amps, or "AUTO" for autoranging
        """
        ...


@runtime_checkable
class ResistanceInput(Protocol):
    """Measure resistance (IVI-DMM inspired).

    Implemented by: DMM with 2-wire or 4-wire capability
    """

    def measure_resistance(self, four_wire: bool = False) -> Decimal:
        """Measure resistance and return the reading.

        Args:
            four_wire: If True, use 4-wire (Kelvin) measurement for higher accuracy

        Returns:
            Measured resistance in Ohms
        """
        ...

    def configure_resistance_range(self, range_val: Decimal | str) -> None:
        """Configure the resistance measurement range.

        Args:
            range_val: Range value in Ohms, or "AUTO" for autoranging
        """
        ...


@runtime_checkable
class FrequencyInput(Protocol):
    """Measure frequency and period (IVI-DMM extension).

    Implemented by: DMM with frequency counter, Frequency counter
    """

    def measure_frequency(self) -> Decimal:
        """Measure frequency and return the reading.

        Returns:
            Measured frequency in Hz
        """
        ...

    def measure_period(self) -> Decimal:
        """Measure period and return the reading.

        Returns:
            Measured period in seconds
        """
        ...


@runtime_checkable
class TemperatureInput(Protocol):
    """Measure temperature (IVI-DMM extension).

    Implemented by: DMM with temperature measurement, Temperature logger
    """

    def measure_temperature(self, sensor_type: str = "rtd") -> Decimal:
        """Measure temperature and return the reading.

        Args:
            sensor_type: Sensor type - "rtd", "thermocouple", "thermistor"

        Returns:
            Measured temperature in configured units (typically Celsius)
        """
        ...


@runtime_checkable
class WaveformInput(Protocol):
    """Acquire waveforms (IVI-Scope inspired).

    Implemented by: Oscilloscope, Digitizer, DAQ with waveform acquisition
    """

    def initiate_acquisition(self) -> None:
        """Start waveform acquisition.

        After calling this, the instrument waits for a trigger.
        """
        ...

    def fetch_waveform(self, channel: str) -> tuple[list[float], float]:
        """Fetch acquired waveform data.

        Args:
            channel: Channel name (e.g., "CH1", "1")

        Returns:
            Tuple of (data_points, x_increment) where:
            - data_points: List of voltage values
            - x_increment: Time between samples in seconds
        """
        ...

    def configure_acquisition(self, sample_rate: Decimal, record_length: int) -> None:
        """Configure acquisition parameters.

        Args:
            sample_rate: Samples per second
            record_length: Number of samples to acquire
        """
        ...

    def configure_trigger(self, source: str, level: Decimal, slope: str) -> None:
        """Configure trigger settings.

        Args:
            source: Trigger source channel
            level: Trigger level in Volts
            slope: "rising" or "falling"
        """
        ...


# =============================================================================
# Stimulus Capabilities (OUTPUT direction)
# =============================================================================


@runtime_checkable
class VoltageOutput(Protocol):
    """Source DC voltage (IVI-DCPwr inspired).

    Implemented by: Power supply, SMU, DAQ analog output
    """

    def set_voltage(self, voltage: Decimal) -> None:
        """Set the output voltage level.

        Args:
            voltage: Desired voltage in Volts
        """
        ...

    def set_voltage_limit(self, limit: Decimal) -> None:
        """Set the voltage limit (for current-priority mode).

        Args:
            limit: Maximum voltage in Volts
        """
        ...

    def enable_output(self, channel: str | None = None) -> None:
        """Enable the output.

        Args:
            channel: Specific channel, or None for all/default
        """
        ...

    def disable_output(self, channel: str | None = None) -> None:
        """Disable the output.

        Args:
            channel: Specific channel, or None for all/default
        """
        ...

    def measure_output_voltage(self) -> Decimal:
        """Read back the actual output voltage.

        Returns:
            Measured output voltage in Volts
        """
        ...


@runtime_checkable
class CurrentOutput(Protocol):
    """Source or sink DC current (IVI-DCPwr inspired).

    Implemented by: Power supply in current mode, SMU, Current source
    """

    def set_current(self, current: Decimal) -> None:
        """Set the output current level.

        Args:
            current: Desired current in Amps
        """
        ...

    def set_current_limit(self, limit: Decimal) -> None:
        """Set the current limit (for voltage-priority mode).

        Args:
            limit: Maximum current in Amps
        """
        ...

    def enable_output(self, channel: str | None = None) -> None:
        """Enable the output.

        Args:
            channel: Specific channel, or None for all/default
        """
        ...

    def disable_output(self, channel: str | None = None) -> None:
        """Disable the output.

        Args:
            channel: Specific channel, or None for all/default
        """
        ...

    def measure_output_current(self) -> Decimal:
        """Read back the actual output current.

        Returns:
            Measured output current in Amps
        """
        ...


@runtime_checkable
class WaveformOutput(Protocol):
    """Generate waveforms (IVI-FGen inspired).

    Implemented by: Function generator, Arbitrary waveform generator, DAQ with waveform output
    """

    def configure_standard_waveform(
        self, waveform: str, frequency: Decimal, amplitude: Decimal
    ) -> None:
        """Configure a standard waveform.

        Args:
            waveform: Waveform type - "sine", "square", "triangle", "ramp", "pulse"
            frequency: Frequency in Hz
            amplitude: Peak-to-peak amplitude in Volts
        """
        ...

    def configure_arbitrary_waveform(self, data: list[float], sample_rate: Decimal) -> None:
        """Configure an arbitrary waveform.

        Args:
            data: List of voltage values (normalized -1 to +1)
            sample_rate: Samples per second
        """
        ...

    def enable_output(self, channel: str | None = None) -> None:
        """Enable the output.

        Args:
            channel: Specific channel, or None for all/default
        """
        ...

    def disable_output(self, channel: str | None = None) -> None:
        """Disable the output.

        Args:
            channel: Specific channel, or None for all/default
        """
        ...


# =============================================================================
# Electronic Load Capabilities
# =============================================================================


@runtime_checkable
class ConstantCurrentLoad(Protocol):
    """Electronic load - constant current mode.

    The load draws a fixed current regardless of the source voltage.
    """

    def set_load_current(self, current: Decimal) -> None:
        """Set the load current.

        Args:
            current: Desired sink current in Amps
        """
        ...

    def enable_load(self) -> None:
        """Enable the electronic load."""
        ...

    def disable_load(self) -> None:
        """Disable the electronic load."""
        ...

    def measure_voltage(self) -> Decimal:
        """Measure the input voltage.

        Returns:
            Measured voltage in Volts
        """
        ...

    def measure_power(self) -> Decimal:
        """Measure the dissipated power.

        Returns:
            Measured power in Watts
        """
        ...


@runtime_checkable
class ConstantPowerLoad(Protocol):
    """Electronic load - constant power mode.

    The load adjusts current to maintain constant power dissipation.
    """

    def set_load_power(self, power: Decimal) -> None:
        """Set the load power.

        Args:
            power: Desired power in Watts
        """
        ...

    def enable_load(self) -> None:
        """Enable the electronic load."""
        ...

    def disable_load(self) -> None:
        """Disable the electronic load."""
        ...


@runtime_checkable
class ConstantResistanceLoad(Protocol):
    """Electronic load - constant resistance mode.

    The load behaves as a fixed resistance.
    """

    def set_load_resistance(self, resistance: Decimal) -> None:
        """Set the load resistance.

        Args:
            resistance: Desired resistance in Ohms
        """
        ...

    def enable_load(self) -> None:
        """Enable the electronic load."""
        ...

    def disable_load(self) -> None:
        """Disable the electronic load."""
        ...


# =============================================================================
# Digital I/O Capabilities
# =============================================================================


@runtime_checkable
class DigitalInput(Protocol):
    """Read digital signals.

    Implemented by: DAQ digital input, GPIO, Digital I/O card
    """

    def read_digital(self, channel: str | None = None) -> bool | int:
        """Read digital input state.

        Args:
            channel: Specific channel/line, or None for port read

        Returns:
            Single line: bool (True=high, False=low)
            Port read: int (bit pattern)
        """
        ...

    def configure_digital_input(self, channel: str, threshold: Decimal | None = None) -> None:
        """Configure digital input.

        Args:
            channel: Channel/line to configure
            threshold: Logic threshold voltage (if configurable)
        """
        ...


@runtime_checkable
class DigitalOutput(Protocol):
    """Write digital signals.

    Implemented by: DAQ digital output, GPIO, Digital I/O card
    """

    def write_digital(self, value: bool | int, channel: str | None = None) -> None:
        """Write digital output state.

        Args:
            value: Single line: bool; Port write: int (bit pattern)
            channel: Specific channel/line, or None for port write
        """
        ...

    def configure_digital_output(self, channel: str) -> None:
        """Configure digital output.

        Args:
            channel: Channel/line to configure
        """
        ...


# =============================================================================
# Communication Capabilities (for active DUTs)
# =============================================================================


@runtime_checkable
class SerialPort(Protocol):
    """Serial communication (RS-232, RS-485, UART).

    Implemented by: USB-Serial adapter, FTDI device, embedded UART
    """

    def serial_write(self, data: bytes) -> None:
        """Write data to serial port.

        Args:
            data: Bytes to transmit
        """
        ...

    def serial_read(self, count: int, timeout: float | None = None) -> bytes:
        """Read data from serial port.

        Args:
            count: Number of bytes to read
            timeout: Read timeout in seconds, or None for blocking

        Returns:
            Received bytes (may be fewer than requested if timeout)
        """
        ...

    def serial_readline(self, timeout: float | None = None) -> bytes:
        """Read a line from serial port.

        Args:
            timeout: Read timeout in seconds, or None for blocking

        Returns:
            Line including terminator
        """
        ...


@runtime_checkable
class I2CBus(Protocol):
    """I2C communication.

    Implemented by: USB-I2C adapter, embedded I2C controller, FTDI with MPSSE
    """

    def i2c_write(self, address: int, data: bytes) -> None:
        """Write data to I2C device.

        Args:
            address: 7-bit I2C address
            data: Bytes to transmit
        """
        ...

    def i2c_read(self, address: int, count: int) -> bytes:
        """Read data from I2C device.

        Args:
            address: 7-bit I2C address
            count: Number of bytes to read

        Returns:
            Received bytes
        """
        ...

    def i2c_write_register(self, address: int, register: int, data: bytes) -> None:
        """Write to I2C device register.

        Args:
            address: 7-bit I2C address
            register: Register address
            data: Bytes to write
        """
        ...

    def i2c_read_register(self, address: int, register: int, count: int) -> bytes:
        """Read from I2C device register.

        Args:
            address: 7-bit I2C address
            register: Register address
            count: Number of bytes to read

        Returns:
            Received bytes
        """
        ...


@runtime_checkable
class SPIBus(Protocol):
    """SPI communication.

    Implemented by: USB-SPI adapter, embedded SPI controller, FTDI with MPSSE
    """

    def spi_transfer(self, data: bytes) -> bytes:
        """Full-duplex SPI transfer.

        Args:
            data: Bytes to transmit

        Returns:
            Received bytes (same length as transmitted)
        """
        ...

    def spi_configure(self, clock_rate: int, mode: int) -> None:
        """Configure SPI settings.

        Args:
            clock_rate: Clock frequency in Hz
            mode: SPI mode (0-3) defining CPOL and CPHA
        """
        ...
