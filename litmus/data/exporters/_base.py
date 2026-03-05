"""Exporter and StreamingDestination protocols.

These protocols define the contract that all output format plugins must satisfy.
Implementations can be file-based (STDF, HDF5) or network-based (PostgreSQL).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.models import TestRun

if TYPE_CHECKING:
    from litmus.schemas import OutputConfig


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


@runtime_checkable
class StreamingDestination(Protocol):
    """Real-time per-measurement streaming destination.

    Streaming destinations receive measurements as they are recorded,
    rather than waiting for the full TestRun at session end.

    Lifecycle:
        open() → append_row()* → mark_run_boundary() → close()

    The built-in JournalWriter implements this protocol for JSONL streaming.

    Important: A class should implement **either** Exporter or
    StreamingDestination, not both. The framework instantiates them
    separately — combining both on one class creates duplicate output.
    """

    format_name: str

    def open(self, config: OutputConfig, test_run: TestRun) -> None:
        """Open the streaming destination.

        Args:
            config: The OutputConfig model for this output entry.
            test_run: The TestRun with run-level context (DUT, station, operator).
        """
        ...

    def append_row(self, row: MeasurementRow) -> None:
        """Append a single denormalized measurement row.

        Args:
            row: Typed MeasurementRow model. Call row.to_flat_dict() at write boundary.
        """
        ...

    def mark_run_boundary(self, run_id: str) -> None:
        """Notify that a test run has completed.

        Called by ``TestRunLogger.finalize()`` before ``close()``.
        Use for format-specific end-of-run framing (e.g. commit a
        database batch, write a summary record).

        Args:
            run_id: The run ID that just completed.
        """
        ...

    def close(self) -> None:
        """Close the streaming destination and finalize any buffered data."""
        ...
