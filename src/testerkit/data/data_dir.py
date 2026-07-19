"""Global data directory resolution.

Single source of truth for where TesterKit stores its data (events, runs,
channels, uploads). The default is the platform data directory
(``~/.local/share/testerkit`` on Linux, ``AppData/Local/testerkit`` on
Windows). A project ``testerkit.yaml`` can override this, but the
global default ensures all processes on a machine share the same
event bus without coordination.

The dir holds three subsystems — `events/` (durable WAL), `runs/`
(per-run parquet test results), `channels/` (time-series instrument
signals) — plus per-subsystem index DBs and lock/state files. Same
shape as PostgreSQL's ``data_directory`` (PGDATA): one dir, mixed
content (tables + WAL + indexes + state), all "data."

Resolution chain:

1. Explicit ``path`` argument (rare — tests, migration scripts)
2. ``testerkit.yaml`` in CWD ancestors → ``data_dir`` field (if set)
3. ``TESTERKIT_HOME`` environment variable
4. ``platformdirs.user_data_dir("testerkit")``
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs


def resolve_data_dir(path: Path | str | None = None) -> Path:
    """Resolve the data directory.

    Most callers should pass no arguments — the global default is the
    right choice for nearly all cases.
    """
    if path is not None:
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # Check project config (testerkit.yaml in CWD ancestors)
    try:
        from testerkit.connect import _find_project_config

        found = _find_project_config()
        if found:
            root, project = found
            if project.data_dir:
                d = root / project.data_dir
                d.mkdir(parents=True, exist_ok=True)
                return d
    except (ImportError, AttributeError, FileNotFoundError):
        pass

    # Global default
    home = Path(os.environ.get("TESTERKIT_HOME", platformdirs.user_data_dir("testerkit")))
    d = home / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
