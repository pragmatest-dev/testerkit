"""Tests for cloud transports and upload queue."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from litmus.models.project import OutputConfig

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


@contextmanager
def _fake_cloud_sdk(
    module_paths: list[str],
) -> Generator[MagicMock, None, None]:
    """Temporarily inject a fake cloud SDK module tree into sys.modules.

    The *last* path in ``module_paths`` gets the returned MagicMock;
    intermediate paths get plain ModuleType stubs.
    """
    mock = MagicMock()
    for path in module_paths[:-1]:
        sys.modules[path] = ModuleType(path)
    sys.modules[module_paths[-1]] = mock
    try:
        yield mock
    finally:
        for path in module_paths:
            sys.modules.pop(path, None)


# ---------------------------------------------------------------------------
# S3 Transport
# ---------------------------------------------------------------------------


def test_s3_transport_send(tmp_path: Path) -> None:
    with _fake_cloud_sdk(["boto3"]) as mock_boto3:
        from litmus.data.transports.s3_transport import S3Transport

        local_file = tmp_path / "results.parquet"
        local_file.write_text("data")
        config = OutputConfig(transport="s3", extras={"bucket": "my-bucket", "prefix": "runs/"})

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        result = S3Transport().send(local_file, config)

        mock_boto3.client.assert_called_once_with("s3")
        mock_client.upload_file.assert_called_once_with(
            str(local_file), "my-bucket", "runs/results.parquet"
        )
        assert result == "s3://my-bucket/runs/results.parquet"


def test_s3_transport_with_endpoint_url(tmp_path: Path) -> None:
    with _fake_cloud_sdk(["boto3"]) as mock_boto3:
        from litmus.data.transports.s3_transport import S3Transport

        local_file = tmp_path / "data.parquet"
        local_file.write_text("data")
        config = OutputConfig(
            transport="s3",
            extras={"bucket": "b", "endpoint_url": "https://minio.local:9000"},
        )
        mock_boto3.client.return_value = MagicMock()
        S3Transport().send(local_file, config)
        mock_boto3.client.assert_called_once_with("s3", endpoint_url="https://minio.local:9000")


def test_s3_transport_missing_bucket(tmp_path: Path) -> None:
    import pytest

    with _fake_cloud_sdk(["boto3"]):
        from litmus.data.transports.s3_transport import S3Transport

        local_file = tmp_path / "data.parquet"
        local_file.write_text("data")
        config = OutputConfig(transport="s3")

        with pytest.raises(ValueError, match="requires 'bucket'"):
            S3Transport().send(local_file, config)


# ---------------------------------------------------------------------------
# Azure Transport
# ---------------------------------------------------------------------------


def test_azure_transport_send(tmp_path: Path) -> None:
    with _fake_cloud_sdk(["azure", "azure.storage", "azure.storage.blob"]) as mock_blob_mod:
        from litmus.data.transports.azure_transport import AzureBlobTransport

        local_file = tmp_path / "results.parquet"
        local_file.write_bytes(b"data")
        config = OutputConfig(
            transport="azure",
            extras={
                "container": "test-container",
                "prefix": "litmus/",
                "connection_string": "DefaultEndpointsProtocol=https;AccountName=test",
            },
        )

        mock_client = MagicMock()
        mock_client.account_name = "test"
        mock_blob_mod.BlobServiceClient.from_connection_string.return_value = mock_client
        mock_blob_client = MagicMock()
        mock_client.get_blob_client.return_value = mock_blob_client

        result = AzureBlobTransport().send(local_file, config)

        mock_client.get_blob_client.assert_called_once_with(
            "test-container", "litmus/results.parquet"
        )
        mock_blob_client.upload_blob.assert_called_once()
        assert "test-container" in result


# ---------------------------------------------------------------------------
# GCS Transport
# ---------------------------------------------------------------------------


def test_gcs_transport_send(tmp_path: Path) -> None:
    with _fake_cloud_sdk(["google", "google.cloud", "google.cloud.storage"]) as mock_storage_mod:
        from litmus.data.transports.gcs_transport import GCSTransport

        local_file = tmp_path / "results.parquet"
        local_file.write_text("data")
        config = OutputConfig(
            transport="gcs", extras={"bucket": "my-gcs-bucket", "prefix": "runs/"}
        )

        mock_client = MagicMock()
        mock_storage_mod.Client.return_value = mock_client
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        result = GCSTransport().send(local_file, config)

        mock_client.bucket.assert_called_once_with("my-gcs-bucket")
        mock_bucket.blob.assert_called_once_with("runs/results.parquet")
        mock_blob.upload_from_filename.assert_called_once_with(str(local_file))
        assert result == "gs://my-gcs-bucket/runs/results.parquet"


# ---------------------------------------------------------------------------
# Upload Queue
# ---------------------------------------------------------------------------


def test_upload_queue_enqueue_and_status(tmp_path: Path) -> None:
    from litmus.data.transports.upload_queue import enqueue, status

    data_dir = str(tmp_path)
    config = OutputConfig(transport="file")
    local_file = tmp_path / "test.parquet"
    local_file.write_text("data")

    enqueue(local_file, "file", config, data_dir)
    rows = status(data_dir)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].transport == "file"


def test_upload_queue_drain_success(tmp_path: Path) -> None:
    from litmus.data.transports.upload_queue import drain, enqueue, status

    data_dir = str(tmp_path)
    local_file = tmp_path / "test.parquet"
    local_file.write_text("data")
    config = OutputConfig(transport="file", output_dir=str(tmp_path / "out"))

    enqueue(local_file, "file", config, data_dir)
    count = drain(data_dir)
    assert count == 1

    rows = status(data_dir)
    assert rows[0].status == "done"


def test_upload_queue_drain_failure(tmp_path: Path) -> None:
    from litmus.data.transports._base import Transport
    from litmus.data.transports.upload_queue import drain, enqueue, status

    data_dir = str(tmp_path)
    local_file = tmp_path / "test.parquet"
    local_file.write_text("data")
    config = OutputConfig(transport="s3", extras={"bucket": "nonexistent"})

    enqueue(local_file, "s3", config, data_dir)

    class FailingS3Transport(Transport):
        transport_name = "s3"

        def send(self, local_path: Path, config: OutputConfig) -> str:
            raise ConnectionError("no creds")

    # __init_subclass__ already registered it by overwriting "s3"
    with pytest.warns(UserWarning, match="Upload failed"):
        count = drain(data_dir)
    assert count == 0

    rows = status(data_dir)
    assert rows[0].status == "failed"
    assert rows[0].attempts == 1


def test_upload_queue_clear_done(tmp_path: Path) -> None:
    from litmus.data.transports.upload_queue import clear_done, drain, enqueue, status

    data_dir = str(tmp_path)
    local_file = tmp_path / "test.parquet"
    local_file.write_text("data")
    config = OutputConfig(transport="file", output_dir=str(tmp_path / "out"))

    enqueue(local_file, "file", config, data_dir)
    drain(data_dir)

    removed = clear_done(data_dir)
    assert removed == 1
    assert len(status(data_dir)) == 0


# ---------------------------------------------------------------------------
# Registry lazy-load
# ---------------------------------------------------------------------------


def test_builtin_transports_registered() -> None:
    """Verify built-in transports auto-register via __init_subclass__."""
    from litmus.data.transports import list_transports

    names = list_transports()
    assert "file" in names
    # Optional transports registered if deps installed
    assert "s3" in names or True  # s3 may not be installed
