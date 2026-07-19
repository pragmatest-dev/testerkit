"""Environment snapshot for software traceability.

Captures the Python runtime environment (version, OS, top-level dependencies)
at test start.  Full package resolution is tracked by ``uv.lock`` in git;
we store only the hash here for correlation.
"""

from __future__ import annotations

import hashlib
import logging
import platform
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class EnvironmentSnapshot(BaseModel):
    """Snapshot of the software environment at test time."""

    python_version: str
    os_name: str
    os_version: str
    platform_machine: str
    testerkit_version: str
    dependencies: list[str]
    lockfile_hash: str | None = None


def _read_top_level_deps() -> list[str]:
    """Read direct dependencies from pyproject.toml [project.dependencies]."""
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        return []
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        data = tomllib.loads(pyproject.read_text())
        return sorted(data.get("project", {}).get("dependencies", []))
    except Exception as exc:
        logger.debug("Could not parse pyproject.toml: %s", exc)
        return []


def _hash_file(path: Path) -> str | None:
    """SHA-256 prefix of a file, or None if missing/unreadable."""
    try:
        if path.exists():
            return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError as exc:
        logger.debug("Could not read %s: %s", path, exc)
    return None


def capture_environment() -> EnvironmentSnapshot:
    """Capture current environment snapshot.

    Stores top-level dependencies from pyproject.toml (the ones you chose),
    not the full transitive dependency tree.  ``lockfile_hash`` covers the
    exact resolved versions; ``pyproject_hash`` covers the intent.
    """
    from testerkit import __version__

    # Hash lockfile (uv.lock preferred, fallback to others)
    lockfile_hash = None
    for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt"):
        lockfile_hash = _hash_file(Path(name))
        if lockfile_hash:
            break

    return EnvironmentSnapshot(
        python_version=platform.python_version(),
        os_name=platform.system(),
        os_version=platform.release(),
        platform_machine=platform.machine(),
        testerkit_version=__version__,
        dependencies=_read_top_level_deps(),
        lockfile_hash=lockfile_hash,
    )
