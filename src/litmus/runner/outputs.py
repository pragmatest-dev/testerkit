"""Runner-neutral subscriber + transport wiring.

Litmus's ``litmus.yaml: outputs:`` block declares one or more
``OutputConfig`` entries — each names a format (parquet, csv, html,
…) and an optional transport (s3, snowflake, …). Every runner needs
to instantiate the right subscriber per format and wire a transport
callback when present.

The wiring is identical across runners; only the ``EventLog`` /
``ChannelStore`` lifecycle is runner-shaped, which the runner's
plugin handles separately.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from litmus.models.project import OutputConfig


def make_transport_callback(
    output_cfg: OutputConfig,
    results_path: Path,
) -> Callable[[Any], None]:
    """Return a callback that enqueues :class:`OutputFile` instances for transport.

    The transport name (``s3``, ``snowflake``, …) lives on
    ``output_cfg.transport``. Failures during enqueue/drain are caught
    and surfaced as warnings so a transport hiccup never breaks the
    run that produced the output.
    """
    transport_name = output_cfg.transport
    assert transport_name is not None  # caller checks before calling

    def _on_output(output: Any) -> None:
        try:
            from litmus.data.transports.upload_queue import drain, enqueue

            enqueue(output.path, transport_name, output_cfg, str(results_path))
            drain(str(results_path))
        except Exception as exc:
            warnings.warn(
                f"Transport callback failed for {output.path}: {exc}",
                stacklevel=2,
            )

    return _on_output


def find_format_transport_callback(
    format_name: str,
    results_path: Path,
) -> Callable[[Any], None] | None:
    """If ``litmus.yaml`` has an output entry for this format with transport, wire it.

    Returns ``None`` if no ``litmus.yaml`` is present, the file is
    invalid, or no entry for ``format_name`` declares a transport.
    """
    try:
        from litmus.store import load_project_config

        config = load_project_config()
    except Exception:  # noqa: BLE001 — missing/invalid config means no transport
        # No litmus.yaml, YAML parse error, or schema mismatch — transport
        # is an opt-in feature so missing config means "skip transport".
        return None
    for output_cfg in config.outputs:
        if output_cfg.format == format_name and output_cfg.transport:
            return make_transport_callback(output_cfg, results_path)
    return None


def create_subscriber(
    cls: type,
    fmt: str,  # noqa: ARG001  — kept for symmetry / future per-format hooks
    output_cfg: OutputConfig,
    results_path: Path,
    session_id: UUID,  # noqa: ARG001  — kept for symmetry / future per-session hooks
) -> Any:
    """Instantiate a subscriber with the uniform ``(output_dir, *, on_output=)`` contract.

    Every subscriber gets ``output_dir`` (the results root) and an
    optional ``on_output`` callback. The subscriber owns its own
    subfolder under ``output_dir``.
    """
    on_output = make_transport_callback(output_cfg, results_path) if output_cfg.transport else None
    output_dir = Path(output_cfg.default_output_dir())
    return cls(output_dir, on_output=on_output)


def run_configured_outputs(test_run: Any, run_id: str, results_dir: str) -> None:
    """Run configured outputs (exports, reports, transports) from ``litmus.yaml``.

    Errors are caught and surfaced as warnings — output processing
    failures must not mask a successful test run.
    """
    try:
        from litmus.data.output_runner import run_outputs

        run_outputs(test_run, run_id, results_dir)
    except Exception as exc:
        warnings.warn(
            f"Output processing failed: {exc}",
            stacklevel=2,
        )
