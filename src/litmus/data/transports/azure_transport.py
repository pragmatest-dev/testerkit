"""Azure Blob Storage transport.

Credentials: pass ``connection_string`` in config extras, or set the
``AZURE_STORAGE_CONNECTION_STRING`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from litmus.data.transports._base import Transport
from litmus.data.transports._helpers import build_blob_name, require_extra

if TYPE_CHECKING:
    from litmus.models.project import OutputConfig


class AzureBlobTransport(Transport):
    """Upload files to Azure Blob Storage."""

    transport_name = "azure"

    def send(self, local_path: Path, config: OutputConfig) -> str:
        from azure.storage.blob import BlobServiceClient  # pyright: ignore[reportMissingImports]

        conn_str = config.extras.get("connection_string") or os.environ.get(
            "AZURE_STORAGE_CONNECTION_STRING"
        )
        if not conn_str:
            raise ValueError(
                "azure transport requires 'connection_string' in config extras "
                "or AZURE_STORAGE_CONNECTION_STRING environment variable"
            )
        container = require_extra(config, "container", "azure")
        blob_name = build_blob_name(config, local_path)
        client = BlobServiceClient.from_connection_string(conn_str)
        client.get_blob_client(container, blob_name).upload_blob(
            local_path.read_bytes(),
            overwrite=True,
        )
        return f"https://{client.account_name}.blob.core.windows.net/{container}/{blob_name}"
