"""JSON Schema generation for YAML validation.

Walks the typed Pydantic models that back every Litmus YAML file and
emits one ``.schema.json`` file per type, so editors (VS Code via the
Red Hat YAML extension, IntelliJ, etc.) can autocomplete and inline-
validate Litmus YAML files.

The schemas are derived directly from the Pydantic models — no parallel
schema definitions to drift. Fields default to ``extra="forbid"`` so
typos surface in the editor before pytest runs.

The ``GLOB_MAP`` ties each schema to the YAML file pattern that should
use it; consumers like ``litmus init`` write a ``.vscode/settings.json``
mapping each glob to the right schema file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.config import FixtureConfig
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.product import Product
from litmus.models.project import ProfileConfig, ProjectConfig
from litmus.models.station import StationConfig
from litmus.models.test_config import SidecarConfig

FileType = Literal[
    "catalog",
    "product",
    "station",
    "fixture",
    "instrument_asset",
    "project",
    "profile",
    "sidecar",
]

SCHEMA_MAP: dict[FileType, type[BaseModel]] = {
    "catalog": InstrumentCatalogEntry,
    "product": Product,
    "station": StationConfig,
    "fixture": FixtureConfig,
    "instrument_asset": InstrumentAssetFile,
    "project": ProjectConfig,
    "profile": ProfileConfig,
    "sidecar": SidecarConfig,
}


GLOB_MAP: dict[FileType, list[str]] = {
    "project": ["litmus.yaml"],
    "product": ["products/**/*.yaml"],
    "catalog": ["catalog/**/*.yaml"],
    "station": ["stations/**/*.yaml"],
    "fixture": ["fixtures/**/*.yaml"],
    "instrument_asset": ["instruments/**/*.yaml"],
    "profile": ["profiles/**/*.yaml"],
    "sidecar": ["tests/**/test_*.yaml"],
}
"""File-glob patterns each schema applies to.

Consumed by ``litmus init`` to wire ``.vscode/settings.json`` ->
``yaml.schemas``. Patterns are workspace-relative, matching the
Red Hat YAML extension's expectation.
"""


def export_schemas(output_dir: Path) -> list[Path]:
    """Generate JSON Schema files for all YAML types.

    Args:
        output_dir: Directory to write ``.schema.json`` files into.

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


def vscode_yaml_schemas(schemas_subdir: str = ".vscode/schemas") -> dict[str, list[str]]:
    """Return the ``yaml.schemas`` mapping for ``.vscode/settings.json``.

    Maps each ``<schemas_subdir>/<name>.schema.json`` to the list of
    workspace-relative globs that should validate against it. Pass the
    result to ``json.dumps`` and write under the ``"yaml.schemas"`` key.
    """
    return {f"{schemas_subdir}/{name}.schema.json": globs for name, globs in GLOB_MAP.items()}
