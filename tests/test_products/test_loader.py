"""Tests for product YAML loading."""

from pathlib import Path

import pytest

from litmus.execution.limits import derive_limit
from litmus.models.enums import Comparator, Direction, MeasurementFunction
from litmus.models.product import Product
from litmus.products.loader import load_product_driver, resolve_product_driver
from litmus.store import load_product


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
        assert product.description is not None
        assert "buck converter" in product.description

    def test_load_product_characteristics(self, power_board_path):
        """Test loading product characteristics."""
        product = load_product(power_board_path)

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

    def test_characteristic_specs(self, power_board_path):
        """Test that specs are parsed as SpecBand list."""
        product = load_product(power_board_path)
        char = product.characteristics["rail_3v3_output"]

        assert len(char.bands) >= 3

        band = char.get_spec_at({"temperature": 25, "load": 0.1, "input_voltage": 5.0})
        assert band is not None
        assert band.value == 3.3
        assert band.accuracy is not None
        assert band.accuracy.pct_reading == 3.0

    def test_quiescent_current_specs(self, power_board_path):
        """Test quiescent current spec bands."""
        product = load_product(power_board_path)
        char = product.characteristics["quiescent_current"]

        band = char.get_spec_at({"temperature": 25, "load": 0})
        assert band is not None
        assert band.value == 0.010


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

        limit = derive_limit(
            char,
            conditions={"temperature": 25, "load": 0.1, "input_voltage": 5.0},
            guardband_pct=5.0,
            char_id="rail_3v3_output",
        )

        assert limit.nominal == 3.3
        assert limit.units == "V"
        assert limit.comparator == Comparator.GELE

        # 3% pct_reading accuracy: uncertainty = 3.3 * 0.03 = 0.099
        # Spec range: 3.201 to 3.399
        # With 5% guardband on that range (0.198 * 0.05 / 2 = 0.00495)
        uncertainty = 3.3 * 0.03
        spec_low = 3.3 - uncertainty
        spec_high = 3.3 + uncertainty
        range_size = spec_high - spec_low
        guardband = range_size * 0.05 / 2

        expected_low = spec_low + guardband
        expected_high = spec_high - guardband

        assert limit.low == pytest.approx(expected_low)
        assert limit.high == pytest.approx(expected_high)

    def test_loaded_characteristic_has_correct_fields(self, power_board_path):
        """Test that loaded characteristic has expected function and direction."""
        product = load_product(power_board_path)
        char = product.characteristics["rail_3v3_output"]

        assert char.direction == Direction.OUTPUT
        assert char.function == MeasurementFunction.DC_VOLTAGE
        assert char.units == "V"


class TestPartNumber:
    """Tests for part_number field."""

    def test_load_product_with_part_number(self):
        """Test that part_number is loaded from YAML."""
        spec_path = Path(__file__).parent.parent / "fixtures" / "specs" / "base_board.yaml"
        product = load_product(spec_path)
        assert product.part_number == "BASE-001"

    def test_load_product_without_part_number(self):
        """Test that part_number is None when not specified."""
        spec_path = Path(__file__).parent.parent / "fixtures" / "specs" / "power_board.yaml"
        product = load_product(spec_path)
        assert product.part_number is None


class TestProductInheritance:
    """Tests for product variant inheritance via base field."""

    @pytest.fixture
    def specs_dir(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "specs"

    def test_variant_inherits_base_fields(self, specs_dir):
        """Test that variant inherits header fields from base."""
        product = load_product(specs_dir / "variant_inherit_all.yaml", products_dir=specs_dir)
        assert product.id == "variant_inherit_all"
        assert product.name == "Inherited Board"
        assert product.part_number == "BASE-001"
        assert product.description == "Base board for testing inheritance"
        assert product.revision == "A"
        assert product.datasheet == "docs/DS-BASE.pdf"

    def test_variant_inherits_sections(self, specs_dir):
        """Test that variant inherits pins, characteristics from base."""
        product = load_product(specs_dir / "variant_inherit_all.yaml", products_dir=specs_dir)
        assert "VIN" in product.pins
        assert "GND" in product.pins
        assert "VOUT" in product.pins
        assert "output_voltage" in product.characteristics

    def test_variant_overrides_sections(self, specs_dir):
        """Test that variant replaces sections it provides."""
        product = load_product(specs_dir / "variant_board.yaml", products_dir=specs_dir)
        assert product.part_number == "VAR-002"
        assert product.revision == "B"
        char = product.characteristics["output_voltage"]
        assert char.bands[0].accuracy is not None
        assert char.bands[0].accuracy.pct_reading == 2.0
        assert "VIN" in product.pins

    def test_variant_base_field_set(self, specs_dir):
        """Test that base field is set on the variant product."""
        product = load_product(specs_dir / "variant_board.yaml", products_dir=specs_dir)
        assert product.base == "base_board"

    def test_circular_inheritance_raises(self, specs_dir):
        """Test that circular inheritance raises ValueError."""
        with pytest.raises(ValueError, match="[Cc]ircular"):
            load_product(specs_dir / "circular_a.yaml", products_dir=specs_dir)

    def test_missing_base_raises(self, specs_dir):
        """Test that missing base raises ValueError."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            dir=specs_dir,
            delete=False,
        ) as f:
            f.write("id: bad_variant\nbase: nonexistent_base\nname: Bad\n")
            f.flush()
            tmp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="not found"):
                load_product(tmp_path, products_dir=specs_dir)
        finally:
            tmp_path.unlink()


class TestProductDriver:
    """Tests for Product.driver field and driver resolution."""

    def test_product_driver_field(self):
        """Product model accepts a driver field."""
        product = Product(id="test", name="Test", driver="drivers.board.MyBoard")
        assert product.driver == "drivers.board.MyBoard"

    def test_product_no_driver(self):
        """Product without driver defaults to None."""
        product = Product(id="test", name="Test")
        assert product.driver is None

    def test_resolve_product_driver_with_driver(self):
        """resolve_product_driver returns the driver string."""
        product = Product(id="test", name="Test", driver="some.module.Cls")
        assert resolve_product_driver(product) == "some.module.Cls"

    def test_resolve_product_driver_none(self):
        """resolve_product_driver returns None when no driver set."""
        product = Product(id="test", name="Test")
        assert resolve_product_driver(product) is None

    def test_load_product_driver_none(self):
        """load_product_driver returns None when product has no driver."""
        product = Product(id="test", name="Test")
        assert load_product_driver(product) is None

    def test_load_product_driver_import(self):
        """load_product_driver loads a real class from a dotted path."""
        # Use a stdlib class as a stand-in for a driver
        product = Product(
            id="test",
            name="Test",
            driver="collections.OrderedDict",
        )
        cls = load_product_driver(product)
        from collections import OrderedDict

        assert cls is OrderedDict

    def test_load_product_driver_bad_path(self):
        """load_product_driver returns None for an invalid import path."""
        product = Product(
            id="test",
            name="Test",
            driver="nonexistent.module.Cls",
        )
        assert load_product_driver(product) is None


class TestProductDriverInheritance:
    """Tests for driver field inheritance through product base chain."""

    @pytest.fixture
    def specs_dir(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "specs"

    def test_driver_inherited_from_base(self, specs_dir):
        """Variant without driver inherits base product's driver."""
        product = load_product(
            specs_dir / "variant_driver_inherit.yaml",
            products_dir=specs_dir,
        )
        assert product.driver == "drivers.base.BaseDriver"

    def test_driver_overridden_by_variant(self, specs_dir):
        """Variant with its own driver overrides base."""
        product = load_product(
            specs_dir / "variant_driver_override.yaml",
            products_dir=specs_dir,
        )
        assert product.driver == "drivers.variant.VariantDriver"
