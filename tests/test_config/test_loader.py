"""Tests for Litmus configuration loader."""

from decimal import Decimal

import pytest

from litmus.config.loader import (
    load_specifications,
    load_station_instance,
    load_station_types,
    load_yaml,
    resolve_all_limit_refs,
    resolve_limit_ref,
)
from litmus.config.models import Limit, Specification


class TestLoadYaml:
    def test_load_yaml_simple(self, tmp_path):
        yaml_content = """
low: 4.5
high: 5.5
units: V
"""
        yaml_file = tmp_path / "limit.yaml"
        yaml_file.write_text(yaml_content)

        limit = load_yaml(yaml_file, Limit)
        assert limit.low == Decimal("4.5")
        assert limit.high == Decimal("5.5")
        assert limit.units == "V"

    def test_load_yaml_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_yaml(tmp_path / "nonexistent.yaml", Limit)


class TestLoadSpecifications:
    def test_load_specifications(self, tmp_path):
        yaml_content = """
specifications:
  rail_5v:
    id: PWR-RAIL-5V
    description: "5V power rail voltage"
    nominal: 5.0
    tolerance_pct: 5
    units: V
  rail_3v3:
    id: PWR-RAIL-3V3
    description: "3.3V power rail voltage"
    nominal: 3.3
    tolerance_pct: 3
    units: V
"""
        spec_file = tmp_path / "specs.yaml"
        spec_file.write_text(yaml_content)

        specs = load_specifications(spec_file)
        assert "rail_5v" in specs
        assert "rail_3v3" in specs
        assert specs["rail_5v"].nominal == Decimal("5.0")
        assert specs["rail_5v"].tolerance_pct == Decimal("5")
        assert specs["rail_3v3"].nominal == Decimal("3.3")

    def test_load_specifications_with_abs_tolerance(self, tmp_path):
        yaml_content = """
specifications:
  input_current:
    id: PWR-INPUT-I
    description: "Input current"
    nominal: 0.5
    tolerance_abs: 0.1
    units: A
"""
        spec_file = tmp_path / "specs.yaml"
        spec_file.write_text(yaml_content)

        specs = load_specifications(spec_file)
        assert specs["input_current"].tolerance_abs == Decimal("0.1")

    def test_load_specifications_empty(self, tmp_path):
        yaml_content = """
specifications: {}
"""
        spec_file = tmp_path / "specs.yaml"
        spec_file.write_text(yaml_content)

        specs = load_specifications(spec_file)
        assert specs == {}


class TestLoadStationTypes:
    def test_load_station_types(self, tmp_path):
        yaml_content = """
station_types:
  universal_bench:
    description: "Universal test bench with DMM, scope, and power supply"
    instruments:
      dmm:
        type: dmm
        driver: pyvisa
        settings:
          default_range: auto
          nplc: 1
      scope:
        type: oscilloscope
        driver: pyvisa
    capabilities:
      - functional
      - parametric
"""
        base_file = tmp_path / "_base.yaml"
        base_file.write_text(yaml_content)

        station_types = load_station_types(base_file)
        assert "universal_bench" in station_types

        bench = station_types["universal_bench"]
        assert bench.id == "universal_bench"
        assert bench.description == "Universal test bench with DMM, scope, and power supply"
        assert "dmm" in bench.instruments
        assert "scope" in bench.instruments
        assert bench.instruments["dmm"].type == "dmm"
        assert bench.instruments["dmm"].settings["nplc"] == 1
        assert "functional" in bench.capabilities

    def test_load_station_types_empty(self, tmp_path):
        yaml_content = """
station_types: {}
"""
        base_file = tmp_path / "_base.yaml"
        base_file.write_text(yaml_content)

        station_types = load_station_types(base_file)
        assert station_types == {}


class TestLoadStationInstance:
    def test_load_station_instance(self, tmp_path):
        yaml_content = """
station:
  id: station_001
  station_type: universal_bench
  location: "Lab A, Bench 3"
  instruments:
    dmm:
      type: dmm
      resource: "TCPIP::192.168.1.101::INSTR"
    scope:
      type: oscilloscope
      resource: "USB0::0x0957::0x1796::MY54321234::INSTR"
"""
        station_file = tmp_path / "station_001.yaml"
        station_file.write_text(yaml_content)

        station = load_station_instance(station_file)
        assert station.id == "station_001"
        assert station.station_type == "universal_bench"
        assert station.location == "Lab A, Bench 3"
        assert "dmm" in station.instruments
        assert station.instruments["dmm"].resource == "TCPIP::192.168.1.101::INSTR"


class TestResolveLimitRef:
    @pytest.fixture
    def sample_specs(self):
        return {
            "product_a": {
                "rail_5v": Specification(
                    id="PWR-RAIL-5V",
                    description="5V rail",
                    nominal=Decimal("5.0"),
                    tolerance_pct=Decimal("5"),
                    units="V",
                ),
                "rail_3v3": Specification(
                    id="PWR-RAIL-3V3",
                    description="3.3V rail",
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("3"),
                    units="V",
                ),
            }
        }

    def test_resolve_limit_ref(self, sample_specs):
        limit = resolve_limit_ref("specs.product_a.rail_5v", sample_specs)
        assert limit.low == Decimal("4.75")
        assert limit.high == Decimal("5.25")
        assert limit.nominal == Decimal("5.0")
        assert limit.units == "V"

    def test_resolve_limit_ref_invalid_format(self, sample_specs):
        with pytest.raises(ValueError, match="Invalid limit reference format"):
            resolve_limit_ref("invalid.ref", sample_specs)

    def test_resolve_limit_ref_wrong_prefix(self, sample_specs):
        with pytest.raises(ValueError, match="Invalid limit reference format"):
            resolve_limit_ref("config.product_a.rail_5v", sample_specs)

    def test_resolve_limit_ref_product_not_found(self, sample_specs):
        with pytest.raises(ValueError, match="Product not found"):
            resolve_limit_ref("specs.product_b.rail_5v", sample_specs)

    def test_resolve_limit_ref_spec_not_found(self, sample_specs):
        with pytest.raises(ValueError, match="Specification not found"):
            resolve_limit_ref("specs.product_a.rail_12v", sample_specs)


class TestResolveAllLimitRefs:
    @pytest.fixture
    def sample_specs(self):
        return {
            "product_a": {
                "rail_5v": Specification(
                    id="PWR-RAIL-5V",
                    description="5V rail",
                    nominal=Decimal("5.0"),
                    tolerance_pct=Decimal("5"),
                    units="V",
                ),
            }
        }

    def test_resolve_all_limit_refs_simple(self, sample_specs):
        config = {"id": "test_5v", "limit_ref": "specs.product_a.rail_5v"}

        resolved = resolve_all_limit_refs(config, sample_specs)
        assert "limit" in resolved
        assert "limit_ref" not in resolved
        assert resolved["limit"].low == Decimal("4.75")

    def test_resolve_all_limit_refs_nested(self, sample_specs):
        config = {
            "steps": [
                {"id": "step1", "limit_ref": "specs.product_a.rail_5v"},
                {"id": "step2", "description": "no limit"},
            ]
        }

        resolved = resolve_all_limit_refs(config, sample_specs)
        assert resolved["steps"][0]["limit"].nominal == Decimal("5.0")
        assert "limit" not in resolved["steps"][1]

    def test_resolve_all_limit_refs_preserves_other_fields(self, sample_specs):
        config = {
            "id": "test",
            "description": "Test step",
            "limit_ref": "specs.product_a.rail_5v",
            "retry": {"max_attempts": 3},
        }

        resolved = resolve_all_limit_refs(config, sample_specs)
        assert resolved["id"] == "test"
        assert resolved["description"] == "Test step"
        assert resolved["retry"]["max_attempts"] == 3
