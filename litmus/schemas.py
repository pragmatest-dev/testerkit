"""JSON Schema generation for YAML validation.

Generates JSON Schema files from Pydantic models so that editors (VS Code,
IntelliJ) can validate and autocomplete Litmus YAML files.

Wrapper models mirror the actual YAML file structure (root-level sibling
keys) and are used only for schema generation, not for loading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from litmus.catalog.models import InstrumentCatalogEntry
from litmus.config.models import (
    FixtureConfig,
    FixturePoint,
    FunctionCapability,
    TestSequenceConfig,
    TestStepConfig,
)
from litmus.products.models import (
    Characteristic,
    Pin,
    Product,
    SignalGroup,
    TestRequirement,
)

# ---------------------------------------------------------------------------
# Wrapper models matching YAML file structure
# ---------------------------------------------------------------------------


class CatalogFile(BaseModel):
    """Schema for catalog/*.yaml files."""

    catalog_entry: InstrumentCatalogEntry
    capabilities: list[FunctionCapability] = Field(default_factory=list)


class ProductFile(BaseModel):
    """Schema for products/*/spec.yaml files."""

    product: Product
    pins: dict[str, Pin] = Field(default_factory=dict)
    characteristics: dict[str, Characteristic] = Field(default_factory=dict)
    signal_groups: dict[str, SignalGroup] = Field(default_factory=dict)
    test_requirements: dict[str, TestRequirement] = Field(default_factory=dict)


class StationInstrumentConfig(BaseModel):
    """Single instrument entry in a station file."""

    type: str
    driver: str
    resource: str | None = None
    catalog_ref: str | None = None
    mock: bool = False
    channels: list[str] = Field(default_factory=list)
    description: str | None = None
    mock_config: dict[str, Any] = Field(default_factory=dict)


class StationHeader(BaseModel):
    """Station identification block."""

    id: str
    name: str
    location: str | None = None
    description: str | None = None


class StationFile(BaseModel):
    """Schema for stations/*.yaml files."""

    station: StationHeader
    instruments: dict[str, StationInstrumentConfig] = Field(default_factory=dict)
    supported_phases: list[str] = Field(default_factory=list)


class SequenceFile(BaseModel):
    """Schema for sequences/*.yaml files."""

    sequence: TestSequenceConfig
    steps: list[TestStepConfig] = Field(default_factory=list)


class FixtureFile(BaseModel):
    """Schema for fixtures/*.yaml files."""

    fixture: FixtureConfig
    points: dict[str, FixturePoint] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schema map and export
# ---------------------------------------------------------------------------

SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "catalog": CatalogFile,
    "product": ProductFile,
    "station": StationFile,
    "sequence": SequenceFile,
    "fixture": FixtureFile,
}


def export_schemas(output_dir: Path) -> list[Path]:
    """Generate JSON Schema files for all YAML types.

    Args:
        output_dir: Directory to write .schema.json files into.

    Returns:
        List of paths to generated schema files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for name, model in SCHEMA_MAP.items():
        schema = model.model_json_schema()
        path = output_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2) + "\n")
        paths.append(path)

    return paths
