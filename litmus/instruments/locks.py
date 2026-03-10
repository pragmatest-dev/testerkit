"""Per-resource file locking for instrument coordination.

Uses OS-level file locks (via ``filelock``) so that locks auto-release on
process death (including SIGKILL). Lock files live under ``LITMUS_HOME/locks/``
which is machine-global — different projects on the same machine share the
same lock namespace because they share physical instruments.

Limitation: ``filelock`` uses ``fcntl.flock()`` on Linux/macOS, which only
works on a single machine. Cross-machine coordination is future work.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

import platformdirs
from filelock import FileLock, Timeout
from filelock._api import BaseFileLock
from pydantic import BaseModel


def _litmus_home() -> Path:
    return Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))


LOCK_DIR_NAME = "locks"


def _lock_dir() -> Path:
    d = _litmus_home() / LOCK_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize_resource(resource: str) -> str:
    """Turn a resource address into a safe filename component."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", resource.replace("::", "__").replace("/", "_"))


class ResourceMeta(BaseModel):
    """Metadata written into lock files for diagnostics."""

    pid: int
    session_id: UUID
    station_id: str
    role: str
    acquired_at: datetime


class ResourceInUse(Exception):
    """Raised when a resource is locked by another process."""

    def __init__(self, resource: str, holder: ResourceMeta | None = None) -> None:
        self.resource = resource
        self.holder = holder
        if holder:
            msg = (
                f"Resource {resource!r} in use by PID {holder.pid} "
                f"(station={holder.station_id!r}, role={holder.role!r}, "
                f"since {holder.acquired_at.isoformat()})"
            )
        else:
            msg = f"Resource {resource!r} is in use by another process"
        super().__init__(msg)


def acquire_resource(resource: str, meta: ResourceMeta, timeout: float = 0) -> BaseFileLock:
    """Acquire a file lock for a physical resource.

    Args:
        resource: Resource address (e.g. ``GPIB::16::INSTR``).
        meta: Metadata about the acquirer (written into lock file).
        timeout: Seconds to wait. 0 = fail immediately.

    Returns:
        The held ``FileLock`` — caller must keep a reference.

    Raises:
        ResourceInUse: If the lock is held by another process.
    """
    lock_path = _lock_dir() / f"{_sanitize_resource(resource)}.lock"
    lock = FileLock(lock_path)
    try:
        lock.acquire(timeout=timeout)
    except Timeout:
        holder = lock_holder(resource)
        raise ResourceInUse(resource, holder) from None

    # Write metadata into the lock file (cosmetic / diagnostic)
    try:
        lock_path.write_text(meta.model_dump_json())
    except OSError:
        pass  # Non-critical — lock is already held

    return lock


def release_resource(resource: str, lock: BaseFileLock) -> None:
    """Release a resource lock."""
    lock.release()


def lock_holder(resource: str) -> ResourceMeta | None:
    """Read metadata from a lock file. Returns None if unreadable."""
    lock_path = _lock_dir() / f"{_sanitize_resource(resource)}.lock"
    try:
        text = lock_path.read_text().strip()
        if text:
            return ResourceMeta.model_validate_json(text)
    except (OSError, ValueError):
        pass
    return None
