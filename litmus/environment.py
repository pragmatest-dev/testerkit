"""Environment snapshot for software traceability.

Captures the Python runtime environment (version, OS, installed packages)
at test start for SBOM generation and regulatory traceability.
"""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path

from pydantic import BaseModel


class PackageInfo(BaseModel):
    """An installed Python package."""

    name: str
    version: str


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
        """Truncated SHA-256 of sorted name==version pairs."""
        canonical = "\n".join(
            f"{p.name}=={p.version}" for p in sorted(self.packages, key=lambda p: p.name.lower())
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

    # Deduplicate (importlib.metadata can return duplicates)
    seen: set[str] = set()
    unique: list[PackageInfo] = []
    for p in packages:
        key = p.name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    # Hash lockfile if present
    lockfile_hash = None
    for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt"):
        lockfile = Path(name)
        if lockfile.exists():
            lockfile_hash = hashlib.sha256(lockfile.read_bytes()).hexdigest()[:16]
            break

    return EnvironmentSnapshot(
        python_version=platform.python_version(),
        os_name=platform.system(),
        os_version=platform.release(),
        platform_machine=platform.machine(),
        litmus_version=__version__,
        packages=unique,
        lockfile_hash=lockfile_hash,
    )
