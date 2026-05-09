"""Tests for Litmus configuration models."""

import pytest

from litmus.models.capability import Capability, Condition, Control, RangeSpec, Signal
from litmus.models.enums import Direction, MeasurementFunction
from litmus.models.station import InstrumentConfig, StationType
from litmus.models.test_config import (
    FixtureConfig,
    FixtureConnection,
    Limit,
    RetryConfig,
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
        assert config.max_retries == 0
        assert config.delay == 0
        assert config.on is None

    def test_retry_custom(self):
        config = RetryConfig(
            max_retries=2,
            delay=0.5,
            on=["TimeoutError", "ConnectionError"],
        )
        assert config.max_retries == 2
        assert config.delay == 0.5
        assert config.on == ["TimeoutError", "ConnectionError"]


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
