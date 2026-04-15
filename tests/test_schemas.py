"""Tests for JSON Schema export."""

import json
from pathlib import Path

import pytest

from litmus.schema_export import SCHEMA_MAP, export_schemas


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
