"""JSON Schema generation for YAML validation.

Generates JSON Schema files from Pydantic models so that editors (VS Code,
IntelliJ) can validate and autocomplete Litmus YAML files.

Models directly mirror the YAML file structure — fields at root, no wrapping key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from litmus.catalog.models import InstrumentCatalogEntry
from litmus.config.models import (
    FixtureConfig,
    TestSequenceConfig,
)
from litmus.instruments.models import CalibrationInfo, InstrumentInfo
from litmus.products.models import Product

# ---------------------------------------------------------------------------
# Instrument asset file schema (unchanged — already flat)
# ---------------------------------------------------------------------------


class InstrumentAssetFile(BaseModel):
    """Schema for instruments/*.yaml asset files (per-device identity + calibration)."""

    id: str
    protocol: str = "visa"
    driver: str | None = None
    resource: str | None = None
    catalog_ref: str | None = None
    info: InstrumentInfo = Field(default_factory=InstrumentInfo)
    calibration: CalibrationInfo = Field(default_factory=CalibrationInfo)


# ---------------------------------------------------------------------------
# Station schema (flat — all fields at root)
# ---------------------------------------------------------------------------


class StationInstrumentConfig(BaseModel):
    """Single instrument entry in a station file."""

    type: str
    driver: str | None = None  # Optional for mock-only instruments
    resource: str | None = None
    catalog_ref: str | None = None
    mock: bool = False
    channels: list[str] = Field(default_factory=list)
    description: str | None = None
    mock_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def resource_required_for_real_hardware(self) -> StationInstrumentConfig:
        """Validate that resource is provided when not using mock mode."""
        if not self.mock and self.resource is None and self.driver is None:
            raise ValueError(
                "resource or driver is required when mock=False. Either set mock=True, "
                "provide a VISA resource string, or provide a driver path."
            )
        return self


class StationConfig(BaseModel):
    """Schema for stations/*.yaml files — all fields at root."""

    id: str
    name: str
    location: str | None = None
    description: str | None = None
    instruments: dict[str, StationInstrumentConfig] = Field(default_factory=dict)
    supported_phases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Project schema (flat — all fields at root)
# ---------------------------------------------------------------------------


class ReportsConfig(BaseModel):
    """Report generation settings."""

    auto: bool = False
    format: str = "html"
    template: str = "default"
    output_dir: str = "reports"


class ProjectConfig(BaseModel):
    """Schema for litmus.yaml project config files — all fields at root."""

    name: str
    results_dir: str = "results"
    reports: ReportsConfig = Field(default_factory=ReportsConfig)


# ---------------------------------------------------------------------------
# Schema map and export
# ---------------------------------------------------------------------------

FileType = Literal[
    "catalog", "product", "station", "sequence",
    "fixture", "instrument_asset", "project",
]

SCHEMA_MAP: dict[FileType, type[BaseModel]] = {
    "catalog": InstrumentCatalogEntry,
    "product": Product,
    "station": StationConfig,
    "sequence": TestSequenceConfig,
    "fixture": FixtureConfig,
    "instrument_asset": InstrumentAssetFile,
    "project": ProjectConfig,
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
