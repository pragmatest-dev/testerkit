"""Transport registry — maps transport names to Transport instances."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.data.transports._base import Transport

_REGISTRY: dict[str, Transport] = {}


def register_transport(transport: Transport) -> None:
    """Register a transport instance.

    Args:
        transport: An object satisfying the Transport protocol.

    Raises:
        ValueError: If transport_name is empty.
    """
    name = transport.transport_name
    if not name:
        raise ValueError("Transport must have a non-empty transport_name")
    _REGISTRY[name] = transport


def get_transport(transport_name: str) -> Transport:
    """Look up a transport by name.

    Args:
        transport_name: The transport identifier (e.g., "s3", "file").

    Returns:
        The registered Transport instance.

    Raises:
        KeyError: If no transport is registered for the given name.
    """
    if transport_name not in _REGISTRY:
        _try_lazy_load(transport_name)
    if transport_name not in _REGISTRY:
        raise KeyError(
            f"No transport registered for '{transport_name}'. "
            f"Available: {', '.join(sorted(_REGISTRY)) or '(none)'}. "
            f"Install optional deps or call register_transport()."
        )
    return _REGISTRY[transport_name]


def list_transports() -> list[str]:
    """Return sorted list of registered transport names."""
    return sorted(_REGISTRY)


def _try_lazy_load(transport_name: str) -> None:
    """Attempt to import and register a built-in transport module."""
    loaders: dict[str, tuple[str, str]] = {
        "file": ("litmus.data.transports.file_transport", "FileTransport"),
    }
    if transport_name not in loaders:
        return

    module_path, class_name = loaders[transport_name]
    try:
        import importlib

        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        register_transport(cls())
    except ImportError:
        pass
    except Exception as exc:
        import warnings

        warnings.warn(
            f"Failed to load transport '{transport_name}': {exc}",
            stacklevel=2,
        )
