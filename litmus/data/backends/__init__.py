"""Storage backends for test results."""

from litmus.data.backends.parquet import ParquetBackend, ParquetSubscriber

__all__ = ["ParquetBackend", "ParquetSubscriber"]
