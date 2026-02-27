"""JSON Schema generation from Pydantic models for VS Code YAML validation."""

import json
from pathlib import Path
from typing import Any


def generate_schemas(output_dir: Path) -> list[str]:
    """Generate JSON Schema files from Pydantic models.

    Creates schema files that match the YAML file structure (with wrapper keys
    like ``product:`` and ``catalog_entry:``).

    Args:
        output_dir: Directory to write schema files into (created if missing).

    Returns:
        List of created filenames.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created = []

    # Product spec: top-level keys are "product" and "characteristics"
    from litmus.products.models import Product

    product_inner = Product.model_json_schema()
    product_schema = _wrap_schema(
        title="Litmus Product Specification",
        description="Product spec YAML with product header and characteristics.",
        inner_schema=product_inner,
        wrapper_key="product",
        extra_properties={
            "characteristics": {
                "type": "object",
                "description": "Named product characteristics (function, direction, specs).",
                "additionalProperties": True,
            },
            "pins": {
                "type": "object",
                "description": "Named pin definitions.",
                "additionalProperties": True,
            },
        },
    )
    _write(output_dir / "product.schema.json", product_schema)
    created.append("product.schema.json")

    # Catalog entry: top-level key is "catalog_entry"
    from litmus.catalog.loader import InstrumentCatalogEntry

    catalog_inner = InstrumentCatalogEntry.model_json_schema()
    catalog_schema = _wrap_schema(
        title="Litmus Instrument Catalog Entry",
        description="Catalog YAML defining instrument capabilities.",
        inner_schema=catalog_inner,
        wrapper_key="catalog_entry",
    )
    _write(output_dir / "catalog.schema.json", catalog_schema)
    created.append("catalog.schema.json")

    return created


def _wrap_schema(
    *,
    title: str,
    description: str,
    inner_schema: dict[str, Any],
    wrapper_key: str,
    extra_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a Pydantic-generated schema under a YAML top-level key.

    YAML files use ``product: {id: ..., name: ...}`` but the Pydantic model
    describes the inner object. This wraps it so the schema validates the
    full YAML structure.
    """
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "description": description,
        "type": "object",
        "properties": {
            wrapper_key: {
                "type": "object",
                "properties": inner_schema.get("properties", {}),
                "required": inner_schema.get("required", []),
            },
        },
    }
    if extra_properties:
        schema["properties"].update(extra_properties)
    if "$defs" in inner_schema:
        schema["$defs"] = inner_schema["$defs"]
    return schema


def _write(path: Path, schema: dict[str, Any]) -> None:
    """Write JSON schema to file."""
    path.write_text(json.dumps(schema, indent=2) + "\n")
