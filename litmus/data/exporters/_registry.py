"""Exporter registry — maps format names to Exporter instances.

Built-in exporters (csv) are registered at import time.
Optional exporters are registered lazily on first access.
Users can register custom exporters via register_exporter().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.data.exporters._base import Exporter

_REGISTRY: dict[str, Exporter] = {}

# Report formats handled by litmus.reports (not exporters)
_REPORT_FORMATS = {"html", "pdf"}


def register_exporter(exporter: Exporter) -> None:
    """Register an exporter instance.

    Args:
        exporter: An object satisfying the Exporter protocol.
            Must have a ``format_name`` attribute.

    Raises:
        ValueError: If format_name is empty.
    """
    name = exporter.format_name
    if not name:
        raise ValueError("Exporter must have a non-empty format_name")
    if name in _REGISTRY:
        import warnings

        warnings.warn(
            f"Overwriting exporter for format '{name}'",
            stacklevel=2,
        )
    _REGISTRY[name] = exporter


def get_exporter(format_name: str) -> Exporter:
    """Look up an exporter by format name.

    Attempts lazy loading of built-in optional exporters if not already
    registered.

    Args:
        format_name: The format identifier (e.g., "csv", "stdf").

    Returns:
        The registered Exporter instance.

    Raises:
        KeyError: If no exporter is registered for the given format.
    """
    if format_name not in _REGISTRY:
        _try_lazy_load(format_name)
    if format_name not in _REGISTRY:
        raise KeyError(
            f"No exporter registered for format '{format_name}'. "
            f"Available: {', '.join(sorted(_REGISTRY)) or '(none)'}. "
            f"Install optional deps or call register_exporter()."
        )
    return _REGISTRY[format_name]


def get_exporter_class(format_name: str) -> type | None:
    """Look up an exporter class (not instance) by format name.

    Attempts lazy loading if not already registered. Returns the class
    so callers can instantiate fresh objects (avoiding singleton sharing).

    Returns:
        The exporter class, or None if not found.
    """
    if format_name not in _REGISTRY:
        _try_lazy_load(format_name)
    if format_name not in _REGISTRY:
        return None
    return type(_REGISTRY[format_name])


def list_exporters() -> list[str]:
    """Return sorted list of registered exporter format names."""
    return sorted(_REGISTRY)


def is_report_format(format_name: str) -> bool:
    """Check if a format is handled by litmus.reports (not an exporter)."""
    return format_name in _REPORT_FORMATS


def _try_lazy_load(format_name: str) -> None:
    """Attempt to import and register a built-in exporter module."""
    loaders: dict[str, tuple[str, str]] = {
        "csv": ("litmus.data.exporters.csv_exporter", "CsvExporter"),
        "json": ("litmus.data.exporters.json_exporter", "JsonExporter"),
        "stdf": ("litmus.data.exporters.stdf", "StdfExporter"),
        "hdf5": ("litmus.data.exporters.hdf5", "Hdf5Exporter"),
        "tdms": ("litmus.data.exporters.tdms", "TdmsExporter"),
        "mdf4": ("litmus.data.exporters.mdf4", "Mdf4Exporter"),
        "atml": ("litmus.data.exporters.atml", "AtmlExporter"),
    }
    if format_name not in loaders:
        return

    module_path, class_name = loaders[format_name]
    try:
        import importlib

        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        register_exporter(cls())
    except ImportError:
        # Optional dependency not installed — expected
        pass
    except Exception as exc:
        import warnings

        warnings.warn(
            f"Failed to load exporter '{format_name}': {exc}",
            stacklevel=2,
        )
