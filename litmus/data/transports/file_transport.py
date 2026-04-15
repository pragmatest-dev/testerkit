"""File transport — copy to local or network path (stdlib shutil)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from litmus.data.transports._base import Transport

if TYPE_CHECKING:
    from litmus.models.project import OutputConfig


class FileTransport(Transport):
    """Copy a file to a local or network directory."""

    transport_name = "file"

    def send(self, local_path: Path, config: OutputConfig) -> str:
        """Copy local_path to the configured output_dir.

        Args:
            local_path: Path to the file to copy.
            config: OutputConfig with output_dir and extras.

        Returns:
            Path to the copied file.
        """
        output_dir = Path(config.default_output_dir())
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / local_path.name
        shutil.copy2(local_path, dest)
        return str(dest)
