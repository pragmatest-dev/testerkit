"""Tests for capability models."""

from decimal import Decimal

from litmus.capabilities import (
    AccuracySpec,
    Capability,
    Direction,
    Domain,
    InstrumentChannelSpec,
    RangeSpec,
    ResolutionSpec,
    SignalType,
)


class TestDirection:
    """Tests for Direction enum."""

    def test_direction_values(self):
        assert Direction.INPUT.value == "input"
        assert Direction.OUTPUT.value == "output"
        assert Direction.BIDIR.value == "bidir"

    def test_direction_from_string(self):
        assert Direction("input") == Direction.INPUT
        assert Direction("output") == Direction.OUTPUT
        assert Direction("bidir") == Direction.BIDIR


class TestDomain:
    """Tests for Domain enum."""

    def test_basic_electrical_domains(self):
        assert Domain.VOLTAGE.value == "voltage"
        assert Domain.CURRENT.value == "current"
        assert Domain.RESISTANCE.value == "resistance"
        assert Domain.POWER.value == "power"

    def test_reactive_domains(self):
        assert Domain.CAPACITANCE.value == "capacitance"
        assert Domain.INDUCTANCE.value == "inductance"
        assert Domain.IMPEDANCE.value == "impedance"

    def test_frequency_domains(self):
        assert Domain.FREQUENCY.value == "frequency"
        assert Domain.PHASE.value == "phase"

    def test_other_domains(self):
        assert Domain.TIME.value == "time"
        assert Domain.LOGIC.value == "logic"
        assert Domain.TEMPERATURE.value == "temperature"


class TestSignalType:
    """Tests for SignalType enum."""

    def test_signal_type_values(self):
        assert SignalType.DC.value == "dc"
        assert SignalType.AC.value == "ac"
        assert SignalType.PULSED.value == "pulsed"
        assert SignalType.TRANSIENT.value == "transient"


class TestRangeSpec:
    """Tests for RangeSpec model."""

    def test_full_range(self):
        spec = RangeSpec(min=Decimal("0"), max=Decimal("10"), units="V")
        assert spec.min == Decimal("0")
        assert spec.max == Decimal("10")
        assert spec.units == "V"

    def test_units_only(self):
        spec = RangeSpec(units="A")
        assert spec.min is None
        assert spec.max is None
        assert spec.units == "A"


class TestAccuracySpec:
    """Tests for AccuracySpec model."""

    def test_percent_reading(self):
        spec = AccuracySpec(pct_reading=Decimal("0.1"))
        assert spec.pct_reading == Decimal("0.1")
        assert spec.pct_range is None
        assert spec.absolute is None

    def test_combined_accuracy(self):
        spec = AccuracySpec(
            pct_reading=Decimal("0.05"),
            pct_range=Decimal("0.01"),
            absolute=Decimal("0.001"),
        )
        assert spec.pct_reading == Decimal("0.05")
        assert spec.pct_range == Decimal("0.01")
        assert spec.absolute == Decimal("0.001")


class TestResolutionSpec:
    """Tests for ResolutionSpec model."""

    def test_bits_resolution(self):
        spec = ResolutionSpec(bits=16)
        assert spec.bits == 16
        assert spec.digits is None

    def test_digits_resolution(self):
        spec = ResolutionSpec(digits=6.5, units="V")
        assert spec.digits == 6.5
        assert spec.units == "V"

    def test_value_resolution(self):
        spec = ResolutionSpec(value=Decimal("0.001"), units="V")
        assert spec.value == Decimal("0.001")
        assert spec.units == "V"


class TestInstrumentChannelSpec:
    """Tests for InstrumentChannelSpec model."""

    def test_defaults(self):
        spec = InstrumentChannelSpec()
        assert spec.count == 1
        assert spec.simultaneous is False
        assert spec.coupling is None

    def test_multichannel(self):
        spec = InstrumentChannelSpec(count=4, simultaneous=True, coupling="differential")
        assert spec.count == 4
        assert spec.simultaneous is True
        assert spec.coupling == "differential"


class TestCapability:
    """Tests for Capability model."""

    def test_minimal_capability(self):
        cap = Capability(direction=Direction.INPUT, domain=Domain.VOLTAGE)
        assert cap.direction == Direction.INPUT
        assert cap.domain == Domain.VOLTAGE
        assert cap.signal_types == []
        assert cap.features == []

    def test_dmm_voltage_capability(self):
        cap = Capability(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC, SignalType.AC],
            range=RangeSpec(min=Decimal("0"), max=Decimal("1000"), units="V"),
            accuracy=AccuracySpec(pct_reading=Decimal("0.05")),
            resolution=ResolutionSpec(digits=6.5),
            features=["auto_range", "true_rms", "4_wire"],
        )
        assert cap.direction == Direction.INPUT
        assert cap.domain == Domain.VOLTAGE
        assert SignalType.DC in cap.signal_types
        assert SignalType.AC in cap.signal_types
        assert cap.range is not None
        assert cap.range.max == Decimal("1000")
        assert "auto_range" in cap.features
        assert "true_rms" in cap.features

    def test_power_supply_capability(self):
        cap = Capability(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            channels=InstrumentChannelSpec(count=2, simultaneous=True),
            range=RangeSpec(min=Decimal("0"), max=Decimal("30"), units="V"),
            features=["ovp", "ocp", "remote_sense"],
        )
        assert cap.direction == Direction.OUTPUT
        assert cap.channels.count == 2
        assert "ovp" in cap.features

    def test_smu_bidir_capability(self):
        cap = Capability(
            direction=Direction.BIDIR,
            domain=Domain.CURRENT,
            signal_types=[SignalType.DC],
            features=["4_quadrant", "bipolar"],
        )
        assert cap.direction == Direction.BIDIR
        assert "4_quadrant" in cap.features
