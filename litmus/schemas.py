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
    persistent: bool = False
    channels: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    mock_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def resource_required_for_real_hardware(self) -> StationInstrumentConfig:
        """Validate that resource is provided when not using mock mode."""
        if not self.mock and self.resource is None and self.driver is None:
            raise ValueError(
                "resource or driver is required when mock=False. Either set mock=True, "
                "provide a VISA resource string (e.g., 'GPIB::1::INSTR'), "
                "or provide a driver path (e.g., 'pymeasure.instruments.keithley:Keithley2400')."
            )
        return self


class StationConfig(BaseModel):
    """Schema for stations/*.yaml files — all fields at root."""

    id: str
    name: str
    station_type: str | None = None
    location: str | None = None
    description: str | None = None
    instruments: dict[str, StationInstrumentConfig] = Field(default_factory=dict)
    supported_phases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Project schema (flat — all fields at root)
# ---------------------------------------------------------------------------


class OutputConfig(BaseModel):
    """A single output entry in the ``outputs`` list.

    Each entry specifies a format (exporter), a transport, or both:

    .. code-block:: yaml

        outputs:
          - format: html                    # report only
          - format: csv                     # export only
          - format: stdf
            transport: s3                   # export + ship
            bucket: my-results
          - transport: snowflake            # ship Parquet directly

    Extra keys (bucket, server, dsn_env, template, etc.) are passed
    through as format- or transport-specific configuration.

    Note: ``format`` and ``transport`` names are not validated against the
    registries at config time (registries are lazy-loaded). Invalid names
    will raise ``KeyError`` at runtime when the output is executed.
    """

    format: str | None = None
    transport: str | None = None
    output_dir: str | None = None
    template: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_extras(cls, data: Any) -> Any:
        """Collect unknown keys into extras, merging with any explicit extras dict."""
        if not isinstance(data, dict):
            return data
        known = {"format", "transport", "output_dir", "template", "extras"}
        extras = {k: v for k, v in data.items() if k not in known}
        cleaned = {k: v for k, v in data.items() if k in known}
        # Merge any explicitly provided extras
        existing = cleaned.get("extras", {})
        if isinstance(existing, dict):
            extras.update(existing)
        cleaned["extras"] = extras
        return cleaned

    @model_validator(mode="after")
    def _require_format_or_transport(self) -> OutputConfig:
        """At least one of format or transport must be set."""
        if self.format is None and self.transport is None:
            raise ValueError("OutputConfig requires at least one of 'format' or 'transport'")
        return self

    def default_output_dir(self) -> str:
        """Resolve output directory with sensible defaults."""
        if self.output_dir:
            return self.output_dir
        subscriber_dirs = {
            "parquet": "results/parquet",
            "channels": "results/channels",
        }
        if self.format in subscriber_dirs:
            return subscriber_dirs[self.format]
        if self.format in ("html", "pdf"):
            return "reports"
        if self.format:
            return f"results/exports/{self.format}"
        # Transport-only (shipping Parquet) — no local output dir needed
        return "results/exports"


class ProjectConfig(BaseModel):
    """Schema for litmus.yaml project config files — all fields at root."""

    name: str
    results_dir: str | None = None
    default_station: str = "station"
    mock_instruments: bool = False
    outputs: list[OutputConfig] = Field(default_factory=list)


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
