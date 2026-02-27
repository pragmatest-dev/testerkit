"""Tests for JSON Schema generation."""

import json
from pathlib import Path

from litmus.schema import generate_schemas


def test_generate_schemas(tmp_path: Path):
    """generate_schemas creates valid JSON schema files."""
    created = generate_schemas(tmp_path)
    assert "product.schema.json" in created
    assert "catalog.schema.json" in created

    for filename in created:
        path = tmp_path / filename
        assert path.exists()
        schema = json.loads(path.read_text())
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert "properties" in schema


def test_product_schema_has_wrapper_key(tmp_path: Path):
    """Product schema wraps under 'product' key matching YAML structure."""
    generate_schemas(tmp_path)
    schema = json.loads((tmp_path / "product.schema.json").read_text())
    assert "product" in schema["properties"]
    product_props = schema["properties"]["product"]["properties"]
    assert "id" in product_props
    assert "name" in product_props


def test_catalog_schema_has_wrapper_key(tmp_path: Path):
    """Catalog schema wraps under 'catalog_entry' key."""
    generate_schemas(tmp_path)
    schema = json.loads((tmp_path / "catalog.schema.json").read_text())
    assert "catalog_entry" in schema["properties"]
    entry_props = schema["properties"]["catalog_entry"]["properties"]
    assert "id" in entry_props
    assert "manufacturer" in entry_props
