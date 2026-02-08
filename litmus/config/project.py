"""Project-level configuration from litmus.yaml."""

from pathlib import Path
from typing import Any

import yaml


def load_project_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load project configuration from litmus.yaml.

    Args:
        path: Path to litmus.yaml. If None, looks in cwd.

    Returns:
        Parsed config dict, or empty dict if file not found.
    """
    if path is None:
        path = Path.cwd() / "litmus.yaml"
    else:
        path = Path(path)

    if not path.exists():
        return {}

    with open(path) as f:
        data = yaml.safe_load(f)

    return data if isinstance(data, dict) else {}
