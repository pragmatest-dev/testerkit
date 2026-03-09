"""Global results directory resolution.

Single source of truth for where Litmus stores results (events, runs,
channels, uploads).  The default is the platform data directory
(``~/.local/share/litmus`` on Linux, ``AppData/Local/litmus`` on
Windows).  A project ``litmus.yaml`` can override this, but the
global default ensures all processes on a machine share the same
event bus without coordination.

Resolution chain:

1. Explicit ``path`` argument (rare — tests, migration scripts)
2. ``litmus.yaml`` in CWD ancestors → ``results_dir`` field (if set)
3. ``LITMUS_HOME`` environment variable
4. ``platformdirs.user_data_dir("litmus")``
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_results_dir(path: Path | str | None = None) -> Path:
    """Resolve the results directory.

    Most callers should pass no arguments — the global default is the
    right choice for nearly all cases.
    """
    if path is not None:
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # Check project config (litmus.yaml in CWD ancestors)
    try:
        from litmus.connect import _find_project_config

        found = _find_project_config()
        if found:
            root, project = found
            if project.results_dir:
                d = root / project.results_dir
                d.mkdir(parents=True, exist_ok=True)
                return d
    except Exception:
        pass

    # Global default
    import platformdirs

    home = Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))
    d = home / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d
