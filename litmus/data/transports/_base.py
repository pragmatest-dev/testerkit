"""Transport base class for shipping files to remote destinations.

Subclass and set ``transport_name`` to auto-register::

    class MinioTransport(Transport):
        transport_name = "minio"
        def send(self, local_path, config) -> str: ...

Extend via entry points in pyproject.toml::

    [project.entry-points."litmus.transports"]
    minio = "my_package.transports:MinioTransport"
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litmus.models.project import OutputConfig


class Transport:
    """Ship a file to a remote destination.

    Subclass and set ``transport_name`` to auto-register.
    """

    transport_name: str

    _registry: dict[str, Transport] = {}
    """Maps transport_name → transport instance. Populated by __init_subclass__."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "transport_name") and cls.transport_name:
            Transport._registry[cls.transport_name] = cls()

    def send(self, local_path: Path, config: OutputConfig) -> str:
        """Upload a local file to the remote destination.

        Args:
            local_path: Path to the file to ship.
            config: The OutputConfig model for this output entry.

        Returns:
            Identifier for the uploaded file (URL, path, or record ID).
        """
        raise NotImplementedError


def get_transport(transport_name: str) -> Transport:
    """Look up a transport by name.

    Raises:
        KeyError: If no transport is registered for the given name.
    """
    if transport_name not in Transport._registry:
        raise KeyError(
            f"No transport registered for '{transport_name}'. "
            f"Available: {', '.join(sorted(Transport._registry)) or '(none)'}. "
        )
    return Transport._registry[transport_name]


def list_transports() -> list[str]:
    """Return sorted list of registered transport names."""
    return sorted(Transport._registry)
