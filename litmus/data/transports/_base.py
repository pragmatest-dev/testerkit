"""Transport protocol for shipping files to remote destinations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from litmus.schemas import OutputConfig


@runtime_checkable
class Transport(Protocol):
    """Ship a file to a remote destination.

    Attributes:
        transport_name: Short identifier used in litmus.yaml outputs config
            (e.g., "s3", "sftp", "snowflake").
    """

    transport_name: str

    def send(self, local_path: Path, config: OutputConfig) -> str:
        """Upload a local file to the remote destination.

        Args:
            local_path: Path to the file to ship.
            config: The OutputConfig model for this output entry.

        Returns:
            Identifier for the uploaded file (URL, path, or record ID).
        """
        ...
