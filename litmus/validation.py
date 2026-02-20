"""YAML file validation for all Litmus config types."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError


def validate_yaml(path: Path, catalog_dir: Path | None = None) -> list[str]:
    """Validate a single YAML file against its Litmus schema.

    Auto-detects the file type from top-level keys.

    Returns:
        List of error strings (empty means valid).
    """
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        return [f"YAML parse error: {exc}"]
    except FileNotFoundError:
        return [f"File not found: {path}"]

    if not isinstance(data, dict):
        return ["File does not contain a YAML mapping"]

    # Detect type and validate
    if "catalog_entry" in data:
        return _validate_catalog(path, catalog_dir)
    if "product" in data:
        return _validate_pydantic("ProductFile", data)
    if "station" in data:
        return _validate_pydantic("StationFile", data)
    if "sequence" in data:
        return _validate_pydantic("SequenceFile", data)
    if "fixture" in data:
        return _validate_pydantic("FixtureFile", data)

    return [f"Could not determine file type from keys: {', '.join(data.keys())}"]


def _validate_catalog(path: Path, catalog_dir: Path | None) -> list[str]:
    """Validate a catalog entry using the full loader (handles inheritance)."""
    from litmus.catalog.loader import load_catalog_entry

    try:
        load_catalog_entry(path, catalog_dir=catalog_dir)
        return []
    except ValidationError as exc:
        return _format_validation_error(exc)
    except Exception as exc:
        return [str(exc)]


def _validate_pydantic(model_name: str, data: dict) -> list[str]:
    """Validate data against a schema wrapper model."""
    from litmus.schemas import (
        FixtureFile,
        ProductFile,
        SequenceFile,
        StationFile,
    )

    models = {
        "ProductFile": ProductFile,
        "StationFile": StationFile,
        "SequenceFile": SequenceFile,
        "FixtureFile": FixtureFile,
    }
    try:
        models[model_name].model_validate(data)
        return []
    except ValidationError as exc:
        return _format_validation_error(exc)


def _format_validation_error(exc: ValidationError) -> list[str]:
    """Format Pydantic ValidationError into readable strings."""
    errors = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e["loc"])
        msg = e["msg"]
        typ = e["type"]
        inp = e.get("input")
        inp_str = f", input_value={inp!r}" if inp is not None else ""
        errors.append(f"  {loc}\n    {msg} [type={typ}{inp_str}]")
    return errors
