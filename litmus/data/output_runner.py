"""Run configured outputs (reports + transports) at session end.

Called by the pytest plugin after the event log is closed. Subscriber
formats (csv, stdf, etc.) already ran live during the session — this
only handles report formats and transports.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.data.models import TestRun
    from litmus.models.project import OutputConfig


def run_outputs(
    test_run: TestRun,
    run_id: str,
    results_dir: str,
) -> None:
    """Execute configured outputs for a completed test run.

    Reads ``ProjectConfig.outputs`` and runs each entry:
    - Report formats (html, pdf) → litmus.reports
    - Transports → ship the exported file
    - Subscriber formats → skip (already ran live)

    All errors are caught and warned — outputs are best-effort and must
    never fail the test run.
    """
    try:
        from litmus.config.project import load_project_config

        config = load_project_config()
    except FileNotFoundError:
        return
    except Exception as exc:
        warnings.warn(f"Failed to load project config for outputs: {exc}", stacklevel=2)
        return

    if not config.outputs:
        return

    for output_cfg in config.outputs:
        try:
            _run_single_output(output_cfg, test_run, run_id, results_dir)
        except Exception as exc:
            warnings.warn(
                f"Output '{output_cfg.format or output_cfg.transport}' failed: {exc}",
                stacklevel=2,
            )


def _run_single_output(
    output_cfg: OutputConfig,
    test_run: TestRun,
    run_id: str,
    results_dir: str,
) -> None:
    """Execute a single output entry."""
    from litmus.data.exporters import is_report_format
    from litmus.data.subscribers import get_subscriber_class

    fmt = output_cfg.format
    transport_name = output_cfg.transport
    output_dir = output_cfg.default_output_dir()

    exported_path: Path | None = None

    if fmt and is_report_format(fmt):
        # Report formats — delegate to litmus.reports
        from litmus.reports import generate_report, load_run_data

        data = load_run_data(run_id, results_dir)
        exported_path = generate_report(
            data,
            output_dir,
            fmt=fmt,
            template=output_cfg.template or "default",
        )
    elif fmt:
        # Subscriber formats already ran live — skip
        cls = get_subscriber_class(fmt)
        if cls is not None:
            return

    # If transport is configured, ship the file via upload queue
    if transport_name and exported_path:
        if not exported_path.exists():
            warnings.warn(
                f"Report returned non-existent path: {exported_path}",
                stacklevel=2,
            )
        else:
            _enqueue_and_drain(exported_path, transport_name, output_cfg, results_dir)
    elif transport_name and not fmt:
        # Transport-only: ship the Parquet file directly
        from litmus.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(results_dir=Path(results_dir) / "runs")
        pq_file = backend.find_run_file(run_id)
        if pq_file:
            _enqueue_and_drain(pq_file, transport_name, output_cfg, results_dir)


def _enqueue_and_drain(
    local_path: Path,
    transport_name: str,
    output_cfg: OutputConfig,
    results_dir: str,
) -> None:
    """Enqueue an upload and immediately attempt to drain the queue."""
    from litmus.data.transports.upload_queue import drain, enqueue

    enqueue(local_path, transport_name, output_cfg, results_dir)
    drain(results_dir)
