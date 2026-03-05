"""JSON exporter — stdlib, no extra dependencies.

Writes a full TestRun as a structured JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path

from litmus.data.models import TestRun


class JsonExporter:
    """Export TestRun to JSON."""

    format_name = "json"

    def export(self, test_run: TestRun, output_path: Path) -> Path:
        """Write test_run to a JSON file.

        Args:
            test_run: The TestRun model to export.
            output_path: Directory to write the file into.

        Returns:
            Path to the created JSON file.
        """
        output_path.mkdir(parents=True, exist_ok=True)
        run_id_short = str(test_run.id)[:8]
        out_file = output_path / f"{run_id_short}.json"

        # mode="json" produces JSON-safe types (strings for datetimes, etc.)
        data = test_run.model_dump(mode="json")
        out_file.write_text(json.dumps(data, indent=2) + "\n")
        return out_file
