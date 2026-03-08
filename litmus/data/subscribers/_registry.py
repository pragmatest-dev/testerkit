"""Subscriber registry — maps format names to EventSubscriber classes.

Unlike the exporter registry (which stores instances), this registry stores
**classes** because subscribers need fresh state per session.

Built-in subscribers are lazy-loaded on first access.
Users can register custom subscribers via ``register_subscriber()``.
"""

from __future__ import annotations

import importlib
import warnings

_REGISTRY: dict[str, type] = {}

_LAZY: dict[str, tuple[str, str]] = {
    "parquet": ("litmus.data.backends.parquet", "ParquetSubscriber"),
    "sessions": ("litmus.data.sessions", "SessionSubscriber"),
}


def register_subscriber(cls: type) -> None:
    """Register a subscriber class by its ``format_name`` attribute."""
    name: str = getattr(cls, "format_name", "")
    if not name:
        raise ValueError("Subscriber class must have a non-empty format_name")
    _REGISTRY[name] = cls


def get_subscriber_class(format_name: str) -> type | None:
    """Look up a subscriber class by format name, with lazy loading."""
    if format_name not in _REGISTRY:
        _try_lazy_load(format_name)
    return _REGISTRY.get(format_name)


def list_subscribers() -> list[str]:
    """Return sorted list of all known subscriber format names (including lazy)."""
    return sorted(set(_REGISTRY) | set(_LAZY))


def _try_lazy_load(format_name: str) -> None:
    if format_name not in _LAZY:
        return
    module_path, class_name = _LAZY[format_name]
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        _REGISTRY[format_name] = cls
    except ImportError:
        pass  # Optional dependency not installed — expected
    except AttributeError as exc:
        warnings.warn(
            f"Failed to load subscriber '{format_name}': {exc}",
            stacklevel=2,
        )
