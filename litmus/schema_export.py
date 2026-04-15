"""JSON Schema generation for YAML validation.

Walks the domain model types in :mod:`litmus.models` and emits one
``.schema.json`` file per YAML file type, so editors (VS Code, IntelliJ)
can validate and autocomplete Litmus YAML files.

All type definitions live in :mod:`litmus.models`. This module is purely
the generator and the ``FileType`` / ``SCHEMA_MAP`` index.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.config import FixtureConfig, TestSequenceConfig
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.product import Product
from litmus.models.project import ProjectConfig
from litmus.models.station import StationConfig

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
