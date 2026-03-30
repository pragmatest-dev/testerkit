"""S3 transport — upload to AWS S3 or any S3-compatible store."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from litmus.data.transports._base import Transport
from litmus.data.transports._helpers import build_blob_name, require_extra

if TYPE_CHECKING:
    from litmus.schemas import OutputConfig


class S3Transport(Transport):
    """Upload files to S3 or S3-compatible storage (MinIO, R2, etc.)."""

    transport_name = "s3"

    def send(self, local_path: Path, config: OutputConfig) -> str:
        import boto3  # pyright: ignore[reportMissingImports]

        bucket = require_extra(config, "bucket", "s3")
        key = build_blob_name(config, local_path)
        client_kwargs: dict[str, str] = {}
        if endpoint_url := config.extras.get("endpoint_url"):
            client_kwargs["endpoint_url"] = endpoint_url
        boto3.client("s3", **client_kwargs).upload_file(str(local_path), bucket, key)
        return f"s3://{bucket}/{key}"
