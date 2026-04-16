"""YAML file validation for all Litmus config types."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError

from litmus.schema_export import FileType
from litmus.store import FILE_LOADERS, detect_file_type


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
    resolved_type = file_type or detect_file_type(path)

    if resolved_type is None:
        if not path.exists():
            return [f"File not found: {path}"]
        return ["Could not determine file type from YAML structure"]

    if resolved_type == "catalog":
        return _validate_catalog(path, catalog_dir)

    loader = FILE_LOADERS.get(resolved_type)
    if loader is None:
        return [f"Unknown file type: {resolved_type!r}"]
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
