"""Write protocol for measurement data persistence."""

from pathlib import Path
from typing import Protocol, runtime_checkable

import pyarrow as pa


@runtime_checkable
class MeasurementWriter(Protocol):
    """Protocol for measurement batch persistence.

    Implementations receive a fully-constructed RecordBatch and
    file-level metadata. Path determination and row building are
    the caller's responsibility.
    """

    def write_batch(
        self,
        batch: pa.RecordBatch,
        path: Path,
        *,
        file_metadata: dict[bytes, bytes] | None = None,
    ) -> Path:
        """Write a measurement batch to persistent storage.

        Args:
            batch: Arrow RecordBatch with measurement data.
            path: Target file path.
            file_metadata: Optional key-value metadata to attach.

        Returns:
            Path to the written file.
        """
        ...
