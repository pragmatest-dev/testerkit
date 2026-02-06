"""Tests for product YAML loading."""

from pathlib import Path

import pytest

from litmus.config.models import Comparator, Direction, MeasurementFunction
from litmus.products.limits import derive_limit
from litmus.products.loader import load_product, load_products_from_directory


class TestLoadProduct:
    """Tests for load_product function."""

    @pytest.fixture
    def power_board_path(self) -> Path:
        """Path to the sample power board spec."""
        return Path(__file__).parent.parent / "fixtures" / "specs" / "power_board.yaml"

    def test_load_product_metadata(self, power_board_path):
        """Test loading product metadata."""
        product = load_product(power_board_path)

        assert product.id == "power_board_v1"
        assert product.name == "DC-DC Power Board Rev A"
        assert product.revision == "A"
        assert "buck converter" in product.description

    def test_load_product_characteristics(self, power_board_path):
        """Test loading product characteristics."""
        product = load_product(power_board_path)

        # Check we loaded all characteristics
        assert "rail_5v_input" in product.characteristics
        assert "rail_3v3_output" in product.characteristics
        assert "quiescent_current" in product.characteristics
        assert "efficiency" in product.characteristics

    def test_characteristic_direction_output(self, power_board_path):
        """Test that OUTPUT direction is parsed correctly."""
        product = load_product(power_board_path)
        char = product.characteristics["rail_3v3_output"]

        assert char.direction == Direction.OUTPUT
        assert char.function == MeasurementFunction.DC_VOLTAGE
        assert char.units == "V"

    def test_characteristic_direction_input(self, power_board_path):
        """Test that INPUT direction is parsed correctly."""
        product = load_product(power_board_path)
        char = product.characteristics["rail_5v_input"]

        assert char.direction == Direction.INPUT

    def test_characteristic_conditions(self, power_board_path):
        """Test that conditions are parsed with extra params."""
        product = load_product(power_board_path)
        char = product.characteristics["rail_3v3_output"]

        # Should have multiple conditions
        assert len(char.conditions) >= 3

        # Find the room temp, light load condition
        point = char.get_at_conditions(
            {"temperature": 25, "load": 0.1, "input_voltage": 5.0}
        )
        assert point is not None
        assert point.nominal == 3.3
        assert point.tolerance_pct == 3.0

    def test_characteristic_comparator(self, power_board_path):
        """Test that non-default comparators are parsed."""
        product = load_product(power_board_path)
        char = product.characteristics["quiescent_current"]

        point = char.get_at_conditions({"temperature": 25, "load": 0})
        assert point is not None
        assert point.comparator == Comparator.LE
        assert point.high == 0.015

    def test_test_requirements(self, power_board_path):
        """Test loading test requirements."""
        product = load_product(power_board_path)

        assert "verify_output_voltage_room_light" in product.test_requirements
        req = product.test_requirements["verify_output_voltage_room_light"]

        assert req.characteristic_ref == "rail_3v3_output"
        assert req.guardband_pct == 5.0
        assert req.priority == "critical"

    def test_test_requirement_no_characteristic(self, power_board_path):
        """Test loading test requirement with no characteristic (data collection)."""
        product = load_product(power_board_path)

        req = product.test_requirements["characterize_line_regulation"]
        assert req.characteristic_ref is None
        assert req.priority == "optional"


class TestIntegration:
    """Integration tests for product loading and limit derivation."""

    @pytest.fixture
    def power_board_path(self) -> Path:
        """Path to the sample power board spec."""
        return Path(__file__).parent.parent / "fixtures" / "specs" / "power_board.yaml"

    def test_derive_limit_from_loaded_product(self, power_board_path):
        """Test deriving limits from a loaded product."""
        product = load_product(power_board_path)

        char = product.characteristics["rail_3v3_output"]
        req = product.test_requirements["verify_output_voltage_room_light"]

        limit = derive_limit(char, req)

        # Check limit was derived correctly
        assert limit.nominal == 3.3
        assert limit.units == "V"
        assert limit.comparator == Comparator.GELE

        # With 3% tolerance: 3.201 to 3.399
        # With 5% guardband on that range
        spec_low = 3.3 * (1 - 0.03)
        spec_high = 3.3 * (1 + 0.03)
        range_size = spec_high - spec_low
        guardband = range_size * 0.05 / 2

        expected_low = spec_low + guardband
        expected_high = spec_high - guardband

        assert limit.low == expected_low
        assert limit.high == expected_high

    def test_capability_requirement_from_loaded_characteristic(self, power_board_path):
        """Test deriving capability requirement from loaded characteristic."""
        product = load_product(power_board_path)
        char = product.characteristics["rail_3v3_output"]

        cap = char.to_capability_requirement()

        # DUT OUTPUT -> instrument INPUT
        assert cap.direction == Direction.INPUT
        assert cap.function == MeasurementFunction.DC_VOLTAGE
        assert "voltage" in cap.parameters
        # Max nominal is 3.3V, with 20% headroom = 3.96V
        assert float(cap.parameters["voltage"].range.max) == pytest.approx(3.96)


class TestLoadProductsFromDirectory:
    """Tests for loading multiple products from a directory."""

    def test_load_products_directory(self):
        """Test loading all products from specs directory."""
        specs_dir = Path(__file__).parent.parent / "fixtures" / "specs"
        products = load_products_from_directory(specs_dir)

        assert "power_board_v1" in products
        product = products["power_board_v1"]
        assert product.name == "DC-DC Power Board Rev A"
