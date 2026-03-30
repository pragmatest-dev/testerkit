"""Google Cloud Storage transport."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from litmus.data.transports._base import Transport
from litmus.data.transports._helpers import build_blob_name, require_extra

if TYPE_CHECKING:
    from litmus.schemas import OutputConfig


class GCSTransport(Transport):
    """Upload files to Google Cloud Storage."""

    transport_name = "gcs"

    def send(self, local_path: Path, config: OutputConfig) -> str:
        from google.cloud import storage  # pyright: ignore[reportAttributeAccessIssue]

        bucket_name = require_extra(config, "bucket", "gcs")
        blob_name = build_blob_name(config, local_path)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        bucket.blob(blob_name).upload_from_filename(str(local_path))
        return f"gs://{bucket_name}/{blob_name}"
