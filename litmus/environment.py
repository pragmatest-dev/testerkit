"""Environment snapshot for software traceability.

Captures the Python runtime environment (version, OS, installed packages)
at test start for SBOM generation and regulatory traceability.
"""

from __future__ import annotations

import hashlib
import logging
import platform
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class PackageInfo(BaseModel):
    """An installed Python package."""

    name: str
    version: str


def _package_sort_key(pkg: PackageInfo) -> str:
    """Sort key for consistent package ordering (case-insensitive)."""
    return pkg.name.lower()


class EnvironmentSnapshot(BaseModel):
    """Snapshot of the software environment at test time."""

    python_version: str
    os_name: str
    os_version: str
    platform_machine: str
    litmus_version: str
    packages: list[PackageInfo]
    lockfile_hash: str | None = None

    @property
    def fingerprint(self) -> str:
        """Truncated SHA-256 of sorted name==version pairs.

        Sorted case-insensitively for canonical ordering. Truncated to 16 hex
        chars for practical use as a version identifier.
        """
        canonical = "\n".join(
            f"{p.name}=={p.version}" for p in sorted(self.packages, key=_package_sort_key)
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def capture_environment() -> EnvironmentSnapshot:
    """Capture current environment snapshot.

    Uses importlib.metadata (stdlib) — no subprocess calls, ~5ms.
    """
    import importlib.metadata

    from litmus import __version__

    packages = [
        PackageInfo(name=d.metadata["Name"], version=d.metadata["Version"])
        for d in importlib.metadata.distributions()
        if d.metadata["Name"]
    ]

    # Deduplicate (importlib.metadata can return duplicates); preserves first seen
    unique = list({p.name.lower(): p for p in packages}.values())

    # Hash lockfile if present
    lockfile_hash = None
    for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt"):
        lockfile = Path(name)
        try:
            if lockfile.exists():
                lockfile_hash = hashlib.sha256(lockfile.read_bytes()).hexdigest()[:16]
                break
        except OSError as exc:
            logger.debug("Could not read lockfile %s: %s", name, exc)

    return EnvironmentSnapshot(
        python_version=platform.python_version(),
        os_name=platform.system(),
        os_version=platform.release(),
        platform_machine=platform.machine(),
        litmus_version=__version__,
        packages=unique,
        lockfile_hash=lockfile_hash,
    )
