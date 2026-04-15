"""YAML file validation for all Litmus config types."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError

from litmus.schema_export import FileType
from litmus.store import FILE_LOADERS


def validate_yaml(
    path: Path,
    *,
    file_type: FileType | None = None,
    catalog_dir: Path | None = None,
) -> list[str]:
    """Validate a single YAML file against its Litmus schema.

    Args:
        path: Path to the YAML file.
        file_type: Explicit type to validate as. If None, auto-detects
            from top-level keys.
        catalog_dir: Root catalog directory (needed for catalog inheritance).

    Returns:
        List of error strings (empty means valid).
    """
    # Explicit type — skip auto-detection
    if file_type == "catalog":
        return _validate_catalog(path, catalog_dir)
    if file_type == "product":
        return _validate_with_product_loader(path)
    if file_type is not None:
        loader = FILE_LOADERS.get(file_type)
        if loader is None:
            return [f"Unknown file type: {file_type!r}"]
        return _run_loader(loader, path)

    # Auto-detect from file contents
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        return [f"YAML parse error: {exc}"]
    except FileNotFoundError:
        return [f"File not found: {path}"]

    if not isinstance(data, dict):
        return ["File does not contain a YAML mapping"]

    if "catalog_entry" in data:
        return _validate_catalog(path, catalog_dir)
    if "product" in data:
        return _validate_with_product_loader(path)

    loader = _detect_loader(data)
    if loader is None:
        return [f"Could not determine file type from keys: {', '.join(data.keys())}"]

    return _run_loader(loader, path)


def _run_loader(loader: Callable, path: Path) -> list[str]:
    """Run a loader and return validation errors."""
    try:
        loader(path)
        return []
    except ValidationError as exc:
        return _format_validation_error(exc)
    except (yaml.YAMLError, OSError, ValueError) as exc:
        return [str(exc)]


def _detect_loader(data: dict) -> Callable | None:
    """Return the appropriate loader function for the given YAML data."""
    for key in ("station", "sequence", "fixture", "project"):
        if key in data:
            return FILE_LOADERS.get(key)
    if "id" in data and ("protocol" in data or "driver" in data):
        return FILE_LOADERS.get("instrument_asset")
    return None


def _validate_with_product_loader(path: Path) -> list[str]:
    """Validate a product spec through the product loader (handles inheritance)."""
    from litmus.store import load_product

    return _run_loader(load_product, path)


def _validate_catalog(path: Path, catalog_dir: Path | None) -> list[str]:
    """Validate a catalog entry using the full loader (handles inheritance)."""
    from litmus.store import load_catalog_entry

    try:
        load_catalog_entry(path, catalog_dir=catalog_dir)
        return []
    except ValidationError as exc:
        return _format_validation_error(exc)
    except (yaml.YAMLError, OSError, ValueError) as exc:
        return [str(exc)]


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
