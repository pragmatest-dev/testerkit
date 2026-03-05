"""CSV exporter — stdlib, no extra dependencies.

Writes one row per measurement with all metadata denormalized,
including dynamic columns (in_*, out_*, instr_*, custom).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from litmus.data.models import TestRun

# Fixed columns written first (in this order), followed by any
# dynamic columns discovered across all rows.
_FIXED_COLUMNS = [
    "run_id",
    "step_name",
    "step_index",
    "vector_index",
    "attempt",
    "measurement_name",
    "value",
    "units",
    "low_limit",
    "high_limit",
    "nominal",
    "comparator",
    "outcome",
    "spec_id",
    "spec_ref",
    "meas_dut_pin",
    "meas_instrument",
    "dut_serial",
    "station_id",
    "operator_id",
    "test_phase",
]


class CsvExporter:
    """Export TestRun to CSV (one row per measurement).

    Uses ``TestRun.iter_rows()`` → ``to_flat_dict()`` so all dynamic
    columns (``in_*``, ``out_*``, ``instr_*``, custom) are included.
    """

    format_name = "csv"

    def export(self, test_run: TestRun, output_path: Path) -> Path:
        """Write test_run measurements to a CSV file.

        Args:
            test_run: The TestRun model to export.
            output_path: Directory to write the file into.

        Returns:
            Path to the created CSV file.
        """
        output_path.mkdir(parents=True, exist_ok=True)
        run_id_short = str(test_run.id)[:8]
        out_file = output_path / f"{run_id_short}.csv"

        # Flatten all rows first so we can discover every column name.
        flat_rows: list[dict[str, Any]] = []
        extra_keys: list[str] = []
        seen: set[str] = set()
        for mrow in test_run.iter_rows():
            flat = mrow.to_flat_dict()
            flat_rows.append(flat)
            for k in flat:
                if k not in seen:
                    seen.add(k)
                    if k not in _FIXED_COLUMNS:
                        extra_keys.append(k)

        # Fixed columns first, then dynamic columns in discovery order.
        fieldnames = [c for c in _FIXED_COLUMNS if c in seen] + extra_keys

        with out_file.open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore",
            )
            writer.writeheader()
            for row in flat_rows:
                clean: dict[str, Any] = {}
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        clean[k] = v.isoformat()
                    elif v is None:
                        clean[k] = ""
                    else:
                        clean[k] = v
                writer.writerow(clean)

        return out_file
