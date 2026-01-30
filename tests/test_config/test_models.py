"""Tests for Litmus configuration models."""

from decimal import Decimal

from litmus.config.models import (
    FixtureConfig,
    FixturePoint,
    InstrumentConfig,
    InstrumentInstance,
    Limit,
    RetryConfig,
    Specification,
    StationInstance,
    StationType,
    TestSequenceConfig,
    TestStepConfig,
)


class TestLimit:
    def test_limit_creation(self):
        limit = Limit(low=Decimal("4.5"), high=Decimal("5.5"), units="V")
        assert limit.low == Decimal("4.5")
        assert limit.high == Decimal("5.5")
        assert limit.units == "V"
        assert limit.nominal is None
        assert limit.spec_ref is None

    def test_limit_with_all_fields(self):
        limit = Limit(
            low=Decimal("4.5"),
            high=Decimal("5.5"),
            nominal=Decimal("5.0"),
            units="V",
            spec_ref="PWR-RAIL-5V",
        )
        assert limit.nominal == Decimal("5.0")
        assert limit.spec_ref == "PWR-RAIL-5V"

    def test_limit_nominal_only(self):
        limit = Limit(nominal=Decimal("3.3"), units="V")
        assert limit.low is None
        assert limit.high is None
        assert limit.nominal == Decimal("3.3")


class TestSpecification:
    def test_spec_creation(self):
        spec = Specification(
            id="PWR-RAIL-5V",
            description="5V rail",
            nominal=Decimal("5.0"),
            tolerance_pct=Decimal("5"),
            units="V",
        )
        assert spec.id == "PWR-RAIL-5V"
        assert spec.nominal == Decimal("5.0")
        assert spec.tolerance_pct == Decimal("5")

    def test_spec_to_limit_with_pct_tolerance(self):
        spec = Specification(
            id="PWR-RAIL-5V",
            description="5V rail",
            nominal=Decimal("5.0"),
            tolerance_pct=Decimal("5"),
            units="V",
        )
        limit = spec.to_limit()
        assert limit.low == Decimal("4.75")
        assert limit.high == Decimal("5.25")
        assert limit.nominal == Decimal("5.0")
        assert limit.units == "V"
        assert limit.spec_ref == "PWR-RAIL-5V"

    def test_spec_to_limit_with_abs_tolerance(self):
        spec = Specification(
            id="PWR-INPUT-I",
            description="Input current",
            nominal=Decimal("0.5"),
            tolerance_abs=Decimal("0.1"),
            units="A",
        )
        limit = spec.to_limit()
        assert limit.low == Decimal("0.4")
        assert limit.high == Decimal("0.6")
        assert limit.nominal == Decimal("0.5")

    def test_spec_to_limit_with_guardband(self):
        spec = Specification(
            id="PWR-RAIL-5V",
            description="5V rail",
            nominal=Decimal("5.0"),
            tolerance_pct=Decimal("10"),
            units="V",
        )
        # 10% tolerance = ±0.5V, 10% guardband reduces to ±0.45V
        limit = spec.to_limit(guardband_pct=Decimal("10"))
        assert limit.low == Decimal("4.55")
        assert limit.high == Decimal("5.45")

    def test_spec_to_limit_no_tolerance(self):
        spec = Specification(
            id="FIXED-VALUE",
            description="Fixed value spec",
            nominal=Decimal("1.0"),
            units="V",
        )
        limit = spec.to_limit()
        assert limit.low is None
        assert limit.high is None
        assert limit.nominal == Decimal("1.0")

    def test_spec_tolerance_pct_takes_precedence(self):
        spec = Specification(
            id="TEST",
            description="Test",
            nominal=Decimal("10.0"),
            tolerance_pct=Decimal("10"),
            tolerance_abs=Decimal("0.5"),  # Should be ignored
            units="V",
        )
        limit = spec.to_limit()
        # 10% of 10 = 1, so limits should be 9 and 11
        assert limit.low == Decimal("9.0")
        assert limit.high == Decimal("11.0")


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
            points={
                "vcc": FixturePoint(name="VCC", instrument="psu", instrument_channel="CH1"),
                "gnd": FixturePoint(name="GND", instrument="psu", instrument_channel="CH1_GND"),
            },
        )
        assert config.id == "product_a_fixture"
        assert "vcc" in config.points
        assert config.points["vcc"].instrument == "psu"
        # Test backwards compatibility alias
        assert "vcc" in config.channels
        assert config.channels["vcc"].instrument == "psu"


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


class TestTestStepConfig:
    def test_step_with_test(self):
        step = TestStepConfig(
            id="test_5v_rail",
            test="tests/test_power.py::test_5v_rail",
            description="Measure 5V rail voltage",
        )
        assert step.id == "test_5v_rail"
        assert step.test == "tests/test_power.py::test_5v_rail"
        assert step.sequence is None
        assert step.limit is None
        assert step.limit_ref is None

    def test_step_with_sequence(self):
        step = TestStepConfig(
            id="run_smoke",
            sequence="power_board_smoke",
            description="Run smoke tests",
        )
        assert step.sequence == "power_board_smoke"
        assert step.test is None

    def test_step_with_limit(self):
        step = TestStepConfig(
            id="test_5v_rail",
            test="tests/test_power.py::test_5v_rail",
            description="Measure 5V rail voltage",
            measurement_name="rail_5v_voltage",
            limit=Limit(low=Decimal("4.75"), high=Decimal("5.25"), units="V"),
        )
        assert step.limit.low == Decimal("4.75")

    def test_step_with_limit_ref(self):
        step = TestStepConfig(
            id="test_5v_rail",
            test="tests/test_power.py::test_5v_rail",
            description="Measure 5V rail voltage",
            limit_ref="specs.product_a.rail_5v",
            pre_dialog="connect_dut",
            retry=RetryConfig(max_attempts=3, strategy="on_fail"),
        )
        assert step.limit_ref == "specs.product_a.rail_5v"
        assert step.pre_dialog == "connect_dut"
        assert step.retry.max_attempts == 3

    def test_step_requires_test_or_sequence(self):
        import pytest

        with pytest.raises(ValueError, match="must have either 'test' or 'sequence'"):
            TestStepConfig(id="invalid_step", description="No test or sequence")

    def test_step_cannot_have_both(self):
        import pytest

        with pytest.raises(ValueError, match="cannot have both 'test' and 'sequence'"):
            TestStepConfig(
                id="invalid_step",
                test="tests/test.py::test",
                sequence="some_sequence",
            )


class TestTestSequenceConfigModel:
    def test_sequence_config(self):
        sequence = TestSequenceConfig(
            id="product_a_functional",
            description="Product A Functional Test",
            product_family="product_a",
            test_phase="production",
            required_fixture="product_a_fixture",
            steps=[
                TestStepConfig(
                    id="test_5v_rail",
                    test="tests/test_power.py::test_5v_rail",
                    description="Measure 5V rail",
                    limit_ref="specs.product_a.rail_5v",
                ),
                TestStepConfig(
                    id="test_3v3_rail",
                    test="tests/test_power.py::test_3v3_rail",
                    description="Measure 3.3V rail",
                    skip_on=["test_5v_rail"],
                ),
            ],
            dialogs={
                "connect_dut": {
                    "id": "connect_dut",
                    "message": "Connect DUT",
                    "dialog_type": "confirm",
                }
            },
        )
        assert sequence.test_phase == "production"
        assert len(sequence.steps) == 2
        assert "connect_dut" in sequence.dialogs

    def test_sequence_with_composition(self):
        sequence = TestSequenceConfig(
            id="full_test",
            description="Full test sequence",
            steps=[
                TestStepConfig(
                    id="run_smoke",
                    sequence="smoke_tests",
                    description="Run smoke tests first",
                ),
                TestStepConfig(
                    id="load_test",
                    test="tests/test_power.py::test_load",
                ),
            ],
        )
        assert sequence.steps[0].sequence == "smoke_tests"
        assert sequence.steps[1].test == "tests/test_power.py::test_load"

    def test_sequence_optional_fields(self):
        # Composable sequences don't need product_family or test_phase
        sequence = TestSequenceConfig(
            id="smoke_tests",
            description="Basic smoke tests",
            steps=[
                TestStepConfig(id="test_1", test="tests/test.py::test_1"),
            ],
        )
        assert sequence.product_family is None
        assert sequence.test_phase is None
