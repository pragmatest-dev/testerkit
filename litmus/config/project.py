"""Project-level configuration from litmus.yaml."""

from pathlib import Path

from litmus.models.project import ProjectConfig


def load_project_config(path: Path | str | None = None) -> ProjectConfig:
    """Load project configuration from litmus.yaml.

    Args:
        path: Path to litmus.yaml. If None, looks in cwd.

    Returns:
        Validated ProjectConfig model, or default if file not found.
    """
    if path is None:
        path = Path.cwd() / "litmus.yaml"
    else:
        path = Path(path)

    if not path.exists():
        return ProjectConfig(name="litmus")

    from litmus.store import load_project

    return load_project(path)
