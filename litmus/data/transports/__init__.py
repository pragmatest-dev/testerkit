"""Pluggable transports for shipping result files to remote destinations.

Architecture:
    file.parquet/stdf/csv → Transport → S3/SFTP/Snowflake/etc.

Transports ship files created by exporters (or raw Parquet) to remote
storage, warehouses, or APIs. They are composable with exporters:

    TestRun → Exporter (STDF) → file.stdf → Transport (S3) → s3://bucket/file.stdf

Built-in transports:
    file    — copy to local/network path (stdlib shutil)

Optional transports (install via pip install litmus[s3], etc.):
    s3, gcs, azure, sftp, snowflake, bigquery, http
"""

from __future__ import annotations

from litmus.data.transports._base import Transport
from litmus.data.transports._registry import (
    get_transport,
    list_transports,
    register_transport,
)

__all__ = [
    "Transport",
    "get_transport",
    "list_transports",
    "register_transport",
]
