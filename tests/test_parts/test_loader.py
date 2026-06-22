"""Tests for part YAML loading."""

from pathlib import Path

import pytest

from litmus.execution.limits import derive_limit
from litmus.models.enums import Comparator, Direction, MeasurementFunction
from litmus.models.part import Part
from litmus.parts.loader import load_part_driver, resolve_part_driver
from litmus.store import load_part


class TestLoadPart:
    """Tests for load_part function."""

    @pytest.fixture
    def power_board_path(self) -> Path:
        """Path to the sample power board spec."""
        return Path(__file__).parent.parent / "fixtures" / "specs" / "power_board_v1.yaml"

    def test_load_part_metadata(self, power_board_path):
        """Test loading part metadata."""
        part = load_part(power_board_path)

        assert part.id == "power_board_v1"
        assert part.name == "DC-DC Power Board Rev A"
        assert part.revision == "A"
        assert part.description is not None
        assert "buck converter" in part.description

    def test_load_part_characteristics(self, power_board_path):
        """Test loading part characteristics."""
        part = load_part(power_board_path)

        assert "rail_5v_input" in part.characteristics
        assert "rail_3v3_output" in part.characteristics
        assert "quiescent_current" in part.characteristics
        assert "efficiency" in part.characteristics

    def test_characteristic_direction_output(self, power_board_path):
        """Test that OUTPUT direction is parsed correctly."""
        part = load_part(power_board_path)
        char = part.characteristics["rail_3v3_output"]

        assert char.direction == Direction.OUTPUT
        assert char.function == MeasurementFunction.DC_VOLTAGE
        assert char.unit == "V"

    def test_characteristic_direction_input(self, power_board_path):
        """Test that INPUT direction is parsed correctly."""
        part = load_part(power_board_path)
        char = part.characteristics["rail_5v_input"]

        assert char.direction == Direction.INPUT

    def test_characteristic_specs(self, power_board_path):
        """Test that specs are parsed as SpecBand list."""
        part = load_part(power_board_path)
        char = part.characteristics["rail_3v3_output"]

        assert len(char.bands) >= 3

        band = char.get_spec_at({"temperature": 25, "load": 0.1, "input_voltage": 5.0})
        assert band is not None
        assert band.value == 3.3
        assert band.accuracy is not None
        assert band.accuracy.pct_reading == 3.0

    def test_quiescent_current_specs(self, power_board_path):
        """Test quiescent current spec bands."""
        part = load_part(power_board_path)
        char = part.characteristics["quiescent_current"]

        band = char.get_spec_at({"temperature": 25, "load": 0})
        assert band is not None
        assert band.value == 0.010


class TestIntegration:
    """Integration tests for part loading and limit derivation."""

    @pytest.fixture
    def power_board_path(self) -> Path:
        """Path to the sample power board spec."""
        return Path(__file__).parent.parent / "fixtures" / "specs" / "power_board_v1.yaml"

    def test_derive_limit_from_loaded_part(self, power_board_path):
        """Test deriving limits from a loaded part."""
        part = load_part(power_board_path)

        char = part.characteristics["rail_3v3_output"]

        limit = derive_limit(
            char,
            conditions={"temperature": 25, "load": 0.1, "input_voltage": 5.0},
            guardband_pct=5.0,
            char_id="rail_3v3_output",
        )

        assert limit.nominal == 3.3
        assert limit.unit == "V"
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
        part = load_part(power_board_path)
        char = part.characteristics["rail_3v3_output"]

        assert char.direction == Direction.OUTPUT
        assert char.function == MeasurementFunction.DC_VOLTAGE
        assert char.unit == "V"


class TestPartNumber:
    """Tests for part_number field."""

    def test_load_part_with_part_number(self):
        """Test that part_number is loaded from YAML."""
        spec_path = Path(__file__).parent.parent / "fixtures" / "specs" / "base_board.yaml"
        part = load_part(spec_path)
        assert part.part_number == "BASE-001"

    def test_load_part_without_part_number(self):
        """Test that part_number is None when not specified."""
        spec_path = Path(__file__).parent.parent / "fixtures" / "specs" / "power_board_v1.yaml"
        part = load_part(spec_path)
        assert part.part_number is None


class TestPartInheritance:
    """Tests for part variant inheritance via base field."""

    @pytest.fixture
    def specs_dir(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "specs"

    def test_variant_inherits_base_fields(self, specs_dir):
        """Test that variant inherits header fields from base."""
        part = load_part(specs_dir / "variant_inherit_all.yaml", parts_dir=specs_dir)
        assert part.id == "variant_inherit_all"
        assert part.name == "Inherited Board"
        assert part.part_number == "BASE-001"
        assert part.description == "Base board for testing inheritance"
        assert part.revision == "A"
        assert part.datasheet == "docs/DS-BASE.pdf"

    def test_variant_inherits_sections(self, specs_dir):
        """Test that variant inherits pins, characteristics from base."""
        part = load_part(specs_dir / "variant_inherit_all.yaml", parts_dir=specs_dir)
        assert "VIN" in part.pins
        assert "GND" in part.pins
        assert "VOUT" in part.pins
        assert "output_voltage" in part.characteristics

    def test_variant_overrides_sections(self, specs_dir):
        """Test that variant replaces sections it provides."""
        part = load_part(specs_dir / "variant_board.yaml", parts_dir=specs_dir)
        assert part.part_number == "VAR-002"
        assert part.revision == "B"
        char = part.characteristics["output_voltage"]
        assert char.bands[0].accuracy is not None
        assert char.bands[0].accuracy.pct_reading == 2.0
        assert "VIN" in part.pins

    def test_variant_base_field_set(self, specs_dir):
        """Test that base field is set on the variant part."""
        part = load_part(specs_dir / "variant_board.yaml", parts_dir=specs_dir)
        assert part.base == "base_board"

    def test_circular_inheritance_raises(self, specs_dir):
        """Test that circular inheritance raises ValueError."""
        with pytest.raises(ValueError, match="[Cc]ircular"):
            load_part(specs_dir / "circular_a.yaml", parts_dir=specs_dir)

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
                load_part(tmp_path, parts_dir=specs_dir)
        finally:
            tmp_path.unlink()


class TestPartDriver:
    """Tests for Part.driver field and driver resolution."""

    def test_part_driver_field(self):
        """Part model accepts a driver field."""
        part = Part(id="test", name="Test", driver="drivers.board.MyBoard")
        assert part.driver == "drivers.board.MyBoard"

    def test_part_no_driver(self):
        """Part without driver defaults to None."""
        part = Part(id="test", name="Test")
        assert part.driver is None

    def test_resolve_part_driver_with_driver(self):
        """resolve_part_driver returns the driver string."""
        part = Part(id="test", name="Test", driver="some.module.Cls")
        assert resolve_part_driver(part) == "some.module.Cls"

    def test_resolve_part_driver_none(self):
        """resolve_part_driver returns None when no driver set."""
        part = Part(id="test", name="Test")
        assert resolve_part_driver(part) is None

    def test_load_part_driver_none(self):
        """load_part_driver returns None when part has no driver."""
        part = Part(id="test", name="Test")
        assert load_part_driver(part) is None

    def test_load_part_driver_import(self):
        """load_part_driver loads a real class from a dotted path."""
        # Use a stdlib class as a stand-in for a driver
        part = Part(
            id="test",
            name="Test",
            driver="collections.OrderedDict",
        )
        cls = load_part_driver(part)
        from collections import OrderedDict

        assert cls is OrderedDict

    def test_load_part_driver_bad_path(self):
        """load_part_driver returns None for an invalid import path."""
        part = Part(
            id="test",
            name="Test",
            driver="nonexistent.module.Cls",
        )
        assert load_part_driver(part) is None


class TestPartDriverInheritance:
    """Tests for driver field inheritance through part base chain."""

    @pytest.fixture
    def specs_dir(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "specs"

    def test_driver_inherited_from_base(self, specs_dir):
        """Variant without driver inherits base part's driver."""
        part = load_part(
            specs_dir / "variant_driver_inherit.yaml",
            parts_dir=specs_dir,
        )
        assert part.driver == "drivers.base.BaseDriver"

    def test_driver_overridden_by_variant(self, specs_dir):
        """Variant with its own driver overrides base."""
        part = load_part(
            specs_dir / "variant_driver_override.yaml",
            parts_dir=specs_dir,
        )
        assert part.driver == "drivers.variant.VariantDriver"
