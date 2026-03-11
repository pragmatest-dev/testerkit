"""Exporter and EventSubscriber protocols.

These protocols define the contract that all output format plugins must satisfy.
Implementations can be file-based (STDF, HDF5) or network-based (PostgreSQL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from litmus.data.event_log import EventSubscriber
from litmus.data.models import TestRun


@runtime_checkable
class Exporter(Protocol):
    """Convert a TestRun to a target file format.

    Exporters run at session end (from live TestRun) or post-hoc
    (from Parquet via reconstruct_test_run).

    Attributes:
        format_name: Short identifier used in litmus.yaml outputs config
            (e.g., "csv", "stdf", "hdf5").
    """

    format_name: str

    def export(self, test_run: TestRun, output_path: Path) -> Path:
        """Write test_run to target format.

        Args:
            test_run: The TestRun model to export.
            output_path: Directory to write the output file into.

        Returns:
            Path to the created file.
        """
        ...


__all__ = ["Exporter", "EventSubscriber"]
