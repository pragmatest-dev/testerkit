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

import json
import pickle
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from litmus.data.models import Measurement, TestRun, TestVector

# Prefix for path references in columns
REF_PATH_PREFIX = "_ref/"


class JournalWriter:
    """Streams measurements to JSONL for live observability and crash recovery.

    Usage:
        with JournalWriter(results_dir, test_run) as journal:
            # ... during test execution ...
            journal.append(measurement, step_name, step_index, vector)
            # Large data is saved separately:
            ref_path = journal.save_ref(vector_id, "waveform", waveform_data)

    The journal file is flushed after each write for crash safety.
    """

    def __init__(self, results_dir: Path | str, test_run: "TestRun"):
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

    def __enter__(self) -> "JournalWriter":
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

    def append(
        self,
        measurement: "Measurement",
        step_name: str,
        step_index: int,
        vector: "TestVector",
    ) -> None:
        """Append a measurement to the journal.

        Builds a complete row matching the parquet schema and writes it as JSON.
        Flushes immediately for crash safety.

        Args:
            measurement: The Measurement to record
            step_name: Name of the test step
            step_index: Index of the step in the test run
            vector: The TestVector containing params and observations
        """
        if self._file is None:
            raise RuntimeError("Journal not open. Use 'with' statement or call __enter__")
        if self._closed:
            raise RuntimeError("Journal is closed")

        row = self._build_row(measurement, step_name, step_index, vector)

        # Write JSON line
        self._file.write(json.dumps(row, default=self._json_serializer) + "\n")
        self._file.flush()

    def _build_row(
        self,
        measurement: "Measurement",
        step_name: str,
        step_index: int,
        vector: "TestVector",
    ) -> dict[str, Any]:
        """Build a row dict matching the parquet schema."""
        tr = self.test_run

        row = {
            # IDENTITY & TIMING
            "run_id": str(tr.id),
            "run_started_at": tr.started_at.isoformat(),
            "run_ended_at": tr.ended_at.isoformat() if tr.ended_at else None,
            "step_name": step_name,
            "step_index": step_index,
            "vector_index": vector.index,
            "attempt": vector.attempt,
            "vector_started_at": vector.started_at.isoformat(),
            "vector_ended_at": vector.ended_at.isoformat() if vector.ended_at else None,
            # WHO - Operator
            "operator_id": tr.operator_id,
            "operator_name": tr.operator_name,
            # WHAT - DUT
            "dut_serial": tr.dut.serial,
            "dut_part_number": tr.dut.part_number,
            "dut_revision": tr.dut.revision,
            "dut_lot_number": tr.dut.lot_number,
            # WHAT - Product
            "product_id": tr.product_id,
            "product_name": tr.product_name,
            "product_revision": tr.product_revision,
            # WHERE - Station
            "station_id": tr.station_id,
            "station_type": tr.station_type,
            "station_location": tr.station_location,
            # WHERE - Fixture
            "fixture_id": tr.fixture_id,
            # WHAT - Test Context
            "sequence_id": tr.test_sequence_id,
            "test_phase": tr.test_phase,
            "git_commit": tr.git_commit,
            # MEASUREMENT - Core
            "measurement_name": measurement.name,
            "measurement_timestamp": measurement.timestamp.isoformat(),
            "value": measurement.value,
            "units": measurement.units,
            "outcome": measurement.outcome.value if measurement.outcome else None,
            # Limits
            "low_limit": measurement.low_limit,
            "high_limit": measurement.high_limit,
            "nominal": measurement.nominal,
            "comparator": measurement.comparator,
            # Spec traceability
            "spec_id": measurement.spec_id,
            "spec_ref": measurement.spec_ref,
            # MEASUREMENT SIGNAL PATH
            "meas_dut_pin": measurement.dut_pin,
            "meas_fixture_point": measurement.fixture_point,
            "meas_instrument": measurement.instrument_name,
            "meas_instrument_resource": measurement.instrument_resource,
            "meas_instrument_channel": measurement.instrument_channel,
            # ROLLUP OUTCOMES
            "vector_outcome": vector.outcome.value if vector.outcome else None,
            "run_outcome": tr.outcome.value,
        }

        # Add stimulus columns (in_* columns)
        row.update(self._build_stimulus_columns(vector))

        # Add observation columns (out_* columns)
        row.update(self._build_observation_columns(vector))

        # Add custom metadata
        for key, value in tr.custom_metadata.items():
            row[key] = value

        return row

    def _build_stimulus_columns(self, vector: "TestVector") -> dict[str, Any]:
        """Build dynamic in_* columns from vector params and stimulus records."""
        cols: dict[str, Any] = {}

        # First, add all vector params as in_{param} columns
        for param, value in vector.params.items():
            if param.startswith("_"):
                continue
            col_name = f"in_{param}"
            cols[col_name] = value

        # Then, overlay stimulus signal path info
        for stim in vector.stimulus:
            param = stim.param
            prefix = f"in_{param}"

            if stim.value is not None:
                cols[prefix] = stim.value
            if stim.instrument:
                cols[f"{prefix}_instrument"] = stim.instrument
            if stim.resource:
                cols[f"{prefix}_resource"] = stim.resource
            if stim.channel:
                cols[f"{prefix}_channel"] = stim.channel
            if stim.dut_pin:
                cols[f"{prefix}_dut_pin"] = stim.dut_pin
            if stim.fixture_point:
                cols[f"{prefix}_fixture_point"] = stim.fixture_point

        return cols

    def _build_observation_columns(self, vector: "TestVector") -> dict[str, Any]:
        """Build dynamic out_* columns from vector observations.

        Scalars are inlined, large data is saved to _ref/ and referenced.
        """
        from litmus.data.models import Waveform

        cols: dict[str, Any] = {}

        if not hasattr(vector, "observations"):
            return cols

        for key, value in vector.observations.items():
            if key.startswith("_"):
                continue

            col_name = f"out_{key}"

            # Scalars → inline
            if isinstance(value, (int, float, str, bool, type(None))):
                cols[col_name] = value

            # Structured types → serialize to _ref/
            elif isinstance(value, Path):
                cols[col_name] = self.save_ref(str(vector.id)[:8], key, value)
            elif isinstance(value, Waveform):
                cols[col_name] = self.save_ref(str(vector.id)[:8], key, value)
            elif isinstance(value, bytes):
                cols[col_name] = self.save_ref(str(vector.id)[:8], key, value)
            elif hasattr(value, "tolist"):
                # numpy array
                cols[col_name] = self.save_ref(str(vector.id)[:8], key, value)
            elif hasattr(value, "model_dump"):
                # Pydantic model
                cols[col_name] = self.save_ref(str(vector.id)[:8], key, value)
            elif isinstance(value, (list, dict)):
                # Small lists/dicts → inline
                cols[col_name] = value
            else:
                # Unknown → inline (may fail on non-serializable types)
                cols[col_name] = value

        return cols

    def save_ref(self, vector_id: str, key: str, value: Any) -> str:
        """Save large data to _ref/ directory and return the reference path.

        Args:
            vector_id: Vector ID prefix (first 8 chars)
            key: Key name for the data
            value: Data to save (Path, Waveform, bytes, ndarray, Pydantic model)

        Returns:
            Reference string like "_ref/abc123_waveform.npz"
        """
        from litmus.data.models import Waveform

        prefix = f"{vector_id}_{key}"

        if isinstance(value, Path):
            ext = value.suffix or ".bin"
            filename = f"{prefix}{ext}"
            shutil.copy(value, self.ref_dir / filename)

        elif isinstance(value, Waveform):
            filename = f"{prefix}.npz"
            try:
                import numpy as np

                np.savez(
                    self.ref_dir / filename,
                    Y=value.Y,
                    t0=value.t0,
                    dt=value.dt,
                    **value.attrs,
                )
            except ImportError:
                filename = f"{prefix}.json"
                (self.ref_dir / filename).write_text(value.model_dump_json())

        elif isinstance(value, bytes):
            filename = f"{prefix}.bin"
            (self.ref_dir / filename).write_bytes(value)

        elif hasattr(value, "model_dump"):
            filename = f"{prefix}.json"
            (self.ref_dir / filename).write_text(value.model_dump_json())

        elif hasattr(value, "tolist"):
            filename = f"{prefix}.npy"
            try:
                import numpy as np

                np.save(self.ref_dir / filename, value)
            except ImportError:
                filename = f"{prefix}.json"
                (self.ref_dir / filename).write_text(json.dumps(value.tolist()))

        else:
            filename = f"{prefix}.pkl"
            with open(self.ref_dir / filename, "wb") as f:
                pickle.dump(value, f)

        return f"{REF_PATH_PREFIX}{filename}"

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
                # Skip corrupted/partial lines (crash recovery scenario)
                pass

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
