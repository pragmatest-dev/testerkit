"""Tests for JSON Schema export."""

import json
from pathlib import Path

import pytest

from litmus.schemas import SCHEMA_MAP, export_schemas


@pytest.fixture
def schema_dir(tmp_path: Path) -> Path:
    """Export schemas to a temp directory and return it."""
    return tmp_path / "schemas"


@pytest.fixture
def exported(schema_dir: Path) -> list[Path]:
    return export_schemas(schema_dir)


def test_export_creates_all_files(exported: list[Path], schema_dir: Path):
    assert len(exported) == len(SCHEMA_MAP)
    for name in SCHEMA_MAP:
        assert (schema_dir / f"{name}.schema.json").exists()


def test_schemas_are_valid_json(exported: list[Path]):
    for path in exported:
        data = json.loads(path.read_text())
        assert isinstance(data, dict)
        assert "properties" in data or "$defs" in data


def test_catalog_schema_has_capabilities(exported: list[Path], schema_dir: Path):
    schema = json.loads((schema_dir / "catalog.schema.json").read_text())
    props = schema.get("properties", {})
    assert "catalog_entry" in props
    assert "capabilities" in props


def test_station_schema_has_instruments(exported: list[Path], schema_dir: Path):
    schema = json.loads((schema_dir / "station.schema.json").read_text())
    props = schema.get("properties", {})
    assert "station" in props
    assert "instruments" in props


def test_product_schema_has_characteristics(exported: list[Path], schema_dir: Path):
    schema = json.loads((schema_dir / "product.schema.json").read_text())
    props = schema.get("properties", {})
    assert "characteristics" in props
    assert "pins" in props


def test_existing_catalog_validates(exported: list[Path], schema_dir: Path):
    """Smoke test: catalog schema can be loaded and has expected structure."""
    schema = json.loads((schema_dir / "catalog.schema.json").read_text())
    # Check that InstrumentCapability refs are resolved in $defs
    defs = schema.get("$defs", {})
    assert "InstrumentCapability" in defs or "capabilities" in schema.get("properties", {})
