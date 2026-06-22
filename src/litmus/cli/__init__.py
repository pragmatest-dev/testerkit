"""Litmus command-line interface."""

from __future__ import annotations

# Import command modules for their registration side effects.
from litmus.cli import (  # noqa: F401,E402
    benchmark_cmd,
    catalog_cmd,
    daemon,
    data_cmd,
    discover_cmd,
    instrument,
    mcp_cmd,
    metrics,
    project,
    refs,
    runs,
    schema_cmd,
    serve_cmd,
    setup_cmd,
    station,
    validate,
)
from litmus.cli.root import main
from litmus.grafana.cli import grafana  # noqa: E402

main.add_command(grafana)

# Re-exported for tests that import these helpers directly.
from litmus.cli.data_cmd import _copy_run_references, _merge_data_dir  # noqa: F401,E402

__all__ = ["main"]
