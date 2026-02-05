"""Tests for the capability matching service."""

from litmus.config.models import Direction, Domain, SignalType
from litmus.matching.service import (
    CapabilityRequirement,
    StationCapability,
    capability_satisfies,
    get_required_capabilities,
    get_station_capabilities,
    match_capabilities,
)
from litmus.products.models import Characteristic, ConditionPoint, Product


class TestCapabilitySatisfies:
    """Tests for the capability_satisfies function."""

    def test_exact_direction_match(self):
        """Station INPUT satisfies required INPUT."""
        station = StationCapability(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            name="voltage_dc",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_bidir_satisfies_input(self):
        """Station BIDIR satisfies required INPUT."""
        station = StationCapability(
            direction=Direction.BIDIR,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            name="voltage",
            instrument_type="smu",
            instrument_name="smu_main",
        )
        required = CapabilityRequirement(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_bidir_satisfies_output(self):
        """Station BIDIR satisfies required OUTPUT."""
        station = StationCapability(
            direction=Direction.BIDIR,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            name="voltage",
            instrument_type="smu",
            instrument_name="smu_main",
        )
        required = CapabilityRequirement(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            characteristic_name="input_voltage",
        )
        assert capability_satisfies(station, required) is True

    def test_direction_mismatch(self):
        """Station INPUT does not satisfy required OUTPUT."""
        station = StationCapability(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            name="voltage_dc",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            characteristic_name="input_voltage",
        )
        assert capability_satisfies(station, required) is False

    def test_domain_mismatch(self):
        """Voltage capability does not satisfy current requirement."""
        station = StationCapability(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            name="voltage_dc",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            direction=Direction.INPUT,
            domain=Domain.CURRENT,
            signal_types=[SignalType.DC],
            characteristic_name="output_current",
        )
        assert capability_satisfies(station, required) is False

    def test_signal_type_overlap(self):
        """Overlapping signal types satisfy requirement."""
        station = StationCapability(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC, SignalType.AC],
            name="voltage",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_signal_type_no_overlap(self):
        """Non-overlapping signal types do not satisfy."""
        station = StationCapability(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.AC],
            name="voltage_ac",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            direction=Direction.INPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is False


class TestMatchCapabilities:
    """Tests for the match_capabilities function."""

    def test_all_requirements_satisfied(self):
        """All requirements satisfied returns compatible=True."""
        required = [
            CapabilityRequirement(
                direction=Direction.INPUT,
                domain=Domain.VOLTAGE,
                signal_types=[SignalType.DC],
                characteristic_name="rail_3v3",
            ),
            CapabilityRequirement(
                direction=Direction.INPUT,
                domain=Domain.CURRENT,
                signal_types=[SignalType.DC],
                characteristic_name="output_current",
            ),
        ]
        available = [
            StationCapability(
                direction=Direction.INPUT,
                domain=Domain.VOLTAGE,
                signal_types=[SignalType.DC],
                name="voltage_dc",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            StationCapability(
                direction=Direction.INPUT,
                domain=Domain.CURRENT,
                signal_types=[SignalType.DC],
                name="current_dc",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
        ]

        result = match_capabilities(required, available)

        assert result.compatible is True
        assert len(result.missing) == 0
        assert len(result.matches) == 2
        assert all(m.satisfied for m in result.matches)

    def test_missing_requirement(self):
        """Missing requirement returns compatible=False."""
        required = [
            CapabilityRequirement(
                direction=Direction.INPUT,
                domain=Domain.VOLTAGE,
                signal_types=[SignalType.DC],
                characteristic_name="rail_3v3",
            ),
            CapabilityRequirement(
                direction=Direction.OUTPUT,
                domain=Domain.CURRENT,
                signal_types=[SignalType.DC],
                characteristic_name="input_current",
            ),
        ]
        available = [
            StationCapability(
                direction=Direction.INPUT,
                domain=Domain.VOLTAGE,
                signal_types=[SignalType.DC],
                name="voltage_dc",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            # No current OUTPUT capability
        ]

        result = match_capabilities(required, available)

        assert result.compatible is False
        assert len(result.missing) == 1
        assert result.missing[0].characteristic_name == "input_current"
        assert result.missing[0].direction == Direction.OUTPUT

    def test_unused_capabilities_tracked(self):
        """Unused station capabilities are tracked."""
        required = [
            CapabilityRequirement(
                direction=Direction.INPUT,
                domain=Domain.VOLTAGE,
                signal_types=[SignalType.DC],
                characteristic_name="rail_3v3",
            ),
        ]
        available = [
            StationCapability(
                direction=Direction.INPUT,
                domain=Domain.VOLTAGE,
                signal_types=[SignalType.DC],
                name="voltage_dc",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            StationCapability(
                direction=Direction.INPUT,
                domain=Domain.CURRENT,
                signal_types=[SignalType.DC],
                name="current_dc",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            StationCapability(
                direction=Direction.INPUT,
                domain=Domain.RESISTANCE,
                signal_types=[],
                name="resistance",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
        ]

        result = match_capabilities(required, available)

        assert result.compatible is True
        assert len(result.unused) == 2
        unused_names = [u.name for u in result.unused]
        assert "current_dc" in unused_names
        assert "resistance" in unused_names


class TestGetRequiredCapabilities:
    """Tests for the get_required_capabilities function."""

    def test_direction_flipping_output_to_input(self):
        """DUT OUTPUT characteristic requires instrument INPUT."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "rail_3v3": Characteristic(
                    direction=Direction.OUTPUT,  # DUT provides this
                    domain=Domain.VOLTAGE,
                    signal_types=[SignalType.DC],
                    units="V",
                    pin="VOUT",
                    conditions=[
                        ConditionPoint(
                            nominal=3.3,
                            tolerance_pct=3.0,
                        )
                    ],
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 1
        req = requirements[0]
        assert req.direction == Direction.INPUT  # Flipped to instrument INPUT
        assert req.domain == Domain.VOLTAGE
        assert SignalType.DC in req.signal_types
        assert req.characteristic_name == "rail_3v3"

    def test_direction_flipping_input_to_output(self):
        """DUT INPUT characteristic requires instrument OUTPUT."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "input_voltage": Characteristic(
                    direction=Direction.INPUT,  # DUT consumes this
                    domain=Domain.VOLTAGE,
                    signal_types=[SignalType.DC],
                    units="V",
                    pin="VIN",
                    conditions=[
                        ConditionPoint(
                            nominal=12.0,
                            tolerance_pct=5.0,
                        )
                    ],
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 1
        req = requirements[0]
        assert req.direction == Direction.OUTPUT  # Flipped to instrument OUTPUT
        assert req.domain == Domain.VOLTAGE

    def test_bidir_stays_bidir(self):
        """DUT BIDIR characteristic requires instrument BIDIR."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "data_line": Characteristic(
                    direction=Direction.BIDIR,
                    domain=Domain.VOLTAGE,
                    signal_types=[SignalType.DC],
                    units="V",
                    pin="DATA",
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 1
        assert requirements[0].direction == Direction.BIDIR

    def test_multiple_characteristics(self):
        """Multiple characteristics generate multiple requirements."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "rail_3v3": Characteristic(
                    direction=Direction.OUTPUT,
                    domain=Domain.VOLTAGE,
                    signal_types=[SignalType.DC],
                    units="V",
                    pin="VOUT_3V3",
                ),
                "rail_5v": Characteristic(
                    direction=Direction.OUTPUT,
                    domain=Domain.VOLTAGE,
                    signal_types=[SignalType.DC],
                    units="V",
                    pin="VOUT_5V",
                ),
                "input_current": Characteristic(
                    direction=Direction.INPUT,
                    domain=Domain.CURRENT,
                    signal_types=[SignalType.DC],
                    units="A",
                    pin="VIN",
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 3
        char_names = [r.characteristic_name for r in requirements]
        assert "rail_3v3" in char_names
        assert "rail_5v" in char_names
        assert "input_current" in char_names


class TestGetStationCapabilities:
    """Tests for the get_station_capabilities function."""

    def test_extracts_capabilities_from_instruments(self, tmp_path, monkeypatch):
        """Capabilities are extracted from station's instruments."""
        # Mock the instrument library loader
        mock_library = {
            "instrument": {"type": "dmm", "name": "DMM"},
            "capabilities": [
                {
                    "name": "voltage_dc",
                    "direction": "input",
                    "domain": "voltage",
                    "signal_types": ["dc"],
                },
                {
                    "name": "current_dc",
                    "direction": "input",
                    "domain": "current",
                    "signal_types": ["dc"],
                },
            ],
        }

        def mock_load_instrument_library(inst_type):
            if inst_type == "dmm":
                return mock_library
            return None

        import litmus.matching.service as service_module

        monkeypatch.setattr(
            service_module, "load_instrument_library", mock_load_instrument_library
        )

        station_config = {
            "station": {
                "id": "test_station",
                "instruments": {
                    "dmm_main": {"type": "dmm", "resource": "GPIB::1"},
                },
            }
        }

        capabilities = get_station_capabilities(station_config)

        assert len(capabilities) == 2
        cap_names = [c.name for c in capabilities]
        assert "voltage_dc" in cap_names
        assert "current_dc" in cap_names
        assert all(c.instrument_name == "dmm_main" for c in capabilities)
        assert all(c.instrument_type == "dmm" for c in capabilities)
