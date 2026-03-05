"""JSONL journal writer for streaming measurements during test execution.

Writes measurements to a JSONL file as they happen, enabling:
- Live observability during test runs
- Crash recovery for interrupted tests
- Real-time UI updates

Directory structure:
    results/.journals/{date}/{timestamp}_{serial}/
    ├── measurements.jsonl     # One line per measurement
    └── _ref/                  # Large data files (waveforms, images)

After successful test completion, journals are converted to parquet
and deleted. See ParquetBackend.convert_journal().
"""

from __future__ import annotations

import json
import warnings
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from litmus.data.backends._row_helpers import MeasurementRow, save_ref_to_dir

if TYPE_CHECKING:
    from litmus.data.models import TestRun
    from litmus.schemas import OutputConfig


class JournalWriter:
    """Streams measurements to JSONL for live observability and crash recovery.

    Implements the StreamingDestination protocol for JSONL output.

    Usage:
        writer = JournalWriter(results_dir, test_run)
        with writer:
            writer.append_row(row)   # MeasurementRow from build_row()

    The journal file is flushed after each write for crash safety.
    """

    format_name = "jsonl"

    def __init__(
        self,
        results_dir: Path | str,
        test_run: TestRun,
    ):
        """Initialize journal writer.

        Args:
            results_dir: Base results directory (e.g., "results")
            test_run: The TestRun object with run metadata
        """
        self.results_dir = Path(results_dir)
        self.test_run = test_run
        self._file = None
        self._closed = False

        # Build journal directory path
        timestamp = test_run.started_at.strftime("%Y%m%dT%H%M%SZ")
        date_str = test_run.started_at.strftime("%Y-%m-%d")
        dut_serial = test_run.dut.serial.strip() if test_run.dut.serial else ""

        if dut_serial:
            dir_name = f"{timestamp}_{dut_serial}"
        else:
            dir_name = timestamp

        self.journal_dir = self.results_dir / ".journals" / date_str / dir_name
        self.journal_path = self.journal_dir / "measurements.jsonl"
        self.ref_dir = self.journal_dir / "_ref"

        # Create directories
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.ref_dir.mkdir(exist_ok=True)

    def __enter__(self) -> JournalWriter:
        """Open the journal file for writing."""
        self._file = open(self.journal_path, "a", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the journal file."""
        self.close()
        return False

    def close(self):
        """Close the journal file."""
        if self._file and not self._closed:
            self._file.close()
            self._closed = True

    # -------------------------------------------------------------------------
    # StreamingDestination protocol methods
    # -------------------------------------------------------------------------

    def open(self, config: OutputConfig, test_run: TestRun) -> None:
        """Open the journal (StreamingDestination protocol).

        The config and test_run parameters are required by the protocol
        but ignored — journal config comes from the constructor.
        """
        if self._file is None:
            self._file = open(self.journal_path, "a", encoding="utf-8")

    def append_row(self, row: MeasurementRow) -> None:
        """Append a MeasurementRow to the journal.

        Flattens the row to a dict for JSONL serialisation and flushes
        immediately for crash safety.

        Args:
            row: Typed MeasurementRow model.
        """
        if self._file is None:
            raise RuntimeError("Journal not open. Call open() first.")
        if self._closed:
            raise RuntimeError("Journal is closed")

        flat = row.to_flat_dict()
        self._file.write(json.dumps(flat, default=self._json_serializer) + "\n")
        self._file.flush()

    def mark_run_boundary(self, run_id: str) -> None:
        """No-op for JSONL — each run gets its own journal file."""

    def save_ref(self, vector_id: str, key: str, value: Any) -> str:
        """Save large data to _ref/ directory and return the reference path.

        Args:
            vector_id: Vector ID prefix (first 8 chars)
            key: Key name for the data
            value: Data to save (Path, Waveform, bytes, ndarray, Pydantic model)

        Returns:
            Reference string like "_ref/abc123_waveform.npz"
        """
        return save_ref_to_dir(self.ref_dir, vector_id, key, value)

    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """JSON serializer for objects not serializable by default."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "tolist"):
            return obj.tolist()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def read_journal(journal_path: Path) -> list[dict[str, Any]]:
    """Read a JSONL journal file and return list of measurement rows.

    Handles partial/corrupted lines gracefully by skipping them.

    Args:
        journal_path: Path to measurements.jsonl file

    Returns:
        List of measurement row dicts
    """
    rows = []
    if not journal_path.exists():
        return rows

    with open(journal_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                warnings.warn(
                    f"{journal_path}:{line_num}: corrupted JSONL line skipped",
                    stacklevel=2,
                )

    return rows


def get_journal_info(journal_dir: Path) -> dict[str, Any] | None:
    """Get metadata about a journal directory.

    Args:
        journal_dir: Path to journal directory containing measurements.jsonl

    Returns:
        Dict with journal metadata, or None if invalid
    """
    journal_path = journal_dir / "measurements.jsonl"
    if not journal_path.exists():
        return None

    rows = read_journal(journal_path)
    if not rows:
        return None

    first_row = rows[0]
    return {
        "journal_dir": str(journal_dir),
        "run_id": first_row.get("run_id"),
        "dut_serial": first_row.get("dut_serial"),
        "station_id": first_row.get("station_id"),
        "started_at": first_row.get("run_started_at"),
        "measurement_count": len(rows),
        "has_ref_files": (journal_dir / "_ref").exists() and any((journal_dir / "_ref").iterdir()),
    }
