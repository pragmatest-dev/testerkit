"""Tests for Litmus configuration models."""

import pytest

from litmus.models.capability import Capability, Condition, Control, RangeSpec, Signal
from litmus.models.enums import Direction, MeasurementFunction
from litmus.models.station_types import (
    InstrumentConfig,
    InstrumentInstance,
    StationInstance,
    StationType,
)
from litmus.models.test_config import (
    FixtureConfig,
    FixtureConnection,
    Limit,
    RetryConfig,
    Specification,
)


class TestLimit:
    def test_limit_creation(self):
        limit = Limit(low=4.5, high=5.5, units="V")
        assert limit.low == 4.5
        assert limit.high == 5.5
        assert limit.units == "V"
        assert limit.nominal is None
        assert limit.spec_ref is None

    def test_limit_with_all_fields(self):
        limit = Limit(
            low=4.5,
            high=5.5,
            nominal=5.0,
            units="V",
            spec_ref="PWR-RAIL-5V",
        )
        assert limit.nominal == 5.0
        assert limit.spec_ref == "PWR-RAIL-5V"

    def test_limit_nominal_only(self):
        limit = Limit(nominal=3.3, units="V")
        assert limit.low is None
        assert limit.high is None
        assert limit.nominal == 3.3


class TestSpecification:
    def test_spec_creation(self):
        spec = Specification(
            id="PWR-RAIL-5V",
            description="5V rail",
            nominal=5.0,
            tolerance_pct=5.0,
            units="V",
        )
        assert spec.id == "PWR-RAIL-5V"
        assert spec.nominal == 5.0
        assert spec.tolerance_pct == 5.0

    def test_spec_to_limit_with_pct_tolerance(self):
        spec = Specification(
            id="PWR-RAIL-5V",
            description="5V rail",
            nominal=5.0,
            tolerance_pct=5.0,
            units="V",
        )
        limit = spec.to_limit()
        assert limit.low == 4.75
        assert limit.high == 5.25
        assert limit.nominal == 5.0
        assert limit.units == "V"
        assert limit.spec_ref == "PWR-RAIL-5V"

    def test_spec_to_limit_with_abs_tolerance(self):
        spec = Specification(
            id="PWR-INPUT-I",
            description="Input current",
            nominal=0.5,
            tolerance_abs=0.1,
            units="A",
        )
        limit = spec.to_limit()
        assert limit.low == 0.4
        assert limit.high == 0.6
        assert limit.nominal == 0.5

    def test_spec_to_limit_with_guardband(self):
        spec = Specification(
            id="PWR-RAIL-5V",
            description="5V rail",
            nominal=5.0,
            tolerance_pct=10.0,
            units="V",
        )
        # 10% tolerance = ±0.5V, 10% guardband reduces to ±0.45V
        limit = spec.to_limit(guardband_pct=10.0)
        assert limit.low == 4.55
        assert limit.high == 5.45

    def test_spec_to_limit_no_tolerance(self):
        spec = Specification(
            id="FIXED-VALUE",
            description="Fixed value spec",
            nominal=1.0,
            units="V",
        )
        limit = spec.to_limit()
        assert limit.low is None
        assert limit.high is None
        assert limit.nominal == 1.0

    def test_spec_tolerance_pct_takes_precedence(self):
        spec = Specification(
            id="TEST",
            description="Test",
            nominal=10.0,
            tolerance_pct=10.0,
            tolerance_abs=0.5,  # Should be ignored
            units="V",
        )
        limit = spec.to_limit()
        # 10% of 10 = 1, so limits should be 9 and 11
        assert limit.low == 9.0
        assert limit.high == 11.0


class TestInstrumentConfig:
    def test_instrument_config_minimal(self):
        config = InstrumentConfig(type="dmm", driver="pyvisa")
        assert config.type == "dmm"
        assert config.driver == "pyvisa"
        assert config.resource is None
        assert config.settings == {}

    def test_instrument_config_with_settings(self):
        config = InstrumentConfig(
            type="dmm",
            driver="pyvisa",
            resource="TCPIP::192.168.1.100::INSTR",
            settings={"nplc": 1, "auto_range": True},
        )
        assert config.resource == "TCPIP::192.168.1.100::INSTR"
        assert config.settings["nplc"] == 1


class TestInstrumentInstance:
    def test_instrument_instance_minimal(self):
        instance = InstrumentInstance(type="dmm", resource="GPIB0::5::INSTR")
        assert instance.type == "dmm"
        assert instance.resource == "GPIB0::5::INSTR"

    def test_instrument_instance_with_resource(self):
        instance = InstrumentInstance(
            type="oscilloscope",
            resource="USB0::0x0957::0x1796::MY54321234::INSTR",
        )
        assert instance.type == "oscilloscope"
        assert instance.resource == "USB0::0x0957::0x1796::MY54321234::INSTR"


class TestStationType:
    def test_station_type(self):
        station_type = StationType(
            id="universal_bench",
            description="Universal test bench",
            instruments={
                "dmm": InstrumentConfig(type="dmm", driver="pyvisa"),
                "psu": InstrumentConfig(type="power_supply", driver="pyvisa"),
            },
            capabilities=["functional", "parametric"],
        )
        assert station_type.id == "universal_bench"
        assert "dmm" in station_type.instruments
        assert "psu" in station_type.instruments
        assert "functional" in station_type.capabilities


class TestStationInstance:
    def test_station_instance_minimal(self):
        instance = StationInstance(id="station_001", station_type="universal_bench")
        assert instance.id == "station_001"
        assert instance.station_type == "universal_bench"
        assert instance.instruments == {}

    def test_station_instance_full(self):
        instance = StationInstance(
            id="station_001",
            station_type="universal_bench",
            location="Lab A, Bench 3",
            instruments={
                "dmm": InstrumentInstance(type="dmm", resource="TCPIP::192.168.1.101::INSTR")
            },
        )
        assert instance.location == "Lab A, Bench 3"
        assert "dmm" in instance.instruments


class TestFixtureConfig:
    def test_fixture_config(self):
        config = FixtureConfig(
            id="product_a_fixture",
            product_family="product_a",
            connections={
                "vcc": FixtureConnection(name="VCC", instrument="psu", instrument_channel="CH1"),
                "gnd": FixtureConnection(
                    name="GND", instrument="psu", instrument_channel="CH1_GND"
                ),
            },
        )
        assert config.id == "product_a_fixture"
        assert "vcc" in config.connections
        assert config.connections["vcc"].instrument == "psu"


class TestRetryConfig:
    def test_retry_defaults(self):
        config = RetryConfig()
        assert config.max_attempts == 1
        assert config.delay_seconds == 0
        assert config.strategy == "on_fail"

    def test_retry_custom(self):
        config = RetryConfig(
            max_attempts=3,
            delay_seconds=0.5,
            strategy="dialog",
            dialog_ref="retry_dialog",
        )
        assert config.max_attempts == 3
        assert config.strategy == "dialog"


class TestCapabilityDisjointNamespaces:
    """Capability must reject overlapping keys across signals/conditions/controls."""

    def _make(self, signals=None, conditions=None, controls=None):
        return Capability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals=signals or {},
            conditions=conditions or {},
            controls=controls or {},
        )

    def test_disjoint_keys_ok(self):
        cap = self._make(
            signals={"voltage": Signal(range=RangeSpec(min=0, max=10, units="V"))},
            conditions={"frequency": Condition(range=RangeSpec(min=1, max=1000, units="Hz"))},
            controls={"coupling": Control(options=["AC", "DC"])},
        )
        assert cap.function == MeasurementFunction.DC_VOLTAGE

    def test_signals_conditions_overlap_rejected(self):
        with pytest.raises(ValueError, match="signals.*conditions"):
            self._make(
                signals={"frequency": Signal(range=RangeSpec(min=0, max=10, units="Hz"))},
                conditions={"frequency": Condition(range=RangeSpec(min=1, max=1000, units="Hz"))},
            )

    def test_signals_controls_overlap_rejected(self):
        with pytest.raises(ValueError, match="signals.*controls"):
            self._make(
                signals={"voltage": Signal(range=RangeSpec(min=0, max=10, units="V"))},
                controls={"voltage": Control(range=RangeSpec(min=0, max=5, units="V"))},
            )

    def test_conditions_controls_overlap_rejected(self):
        with pytest.raises(ValueError, match="conditions.*controls"):
            self._make(
                conditions={"frequency": Condition(range=RangeSpec(min=1, max=1000, units="Hz"))},
                controls={"frequency": Control(options=["50", "60"])},
            )
