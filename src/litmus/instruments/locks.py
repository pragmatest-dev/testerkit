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
import threading
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


_registry: dict[tuple[str, int, UUID, str], tuple[int, BaseFileLock]] = {}
_lock_to_key: dict[int, tuple[str, int, UUID, str]] = {}
_registry_lock = threading.Lock()


def _holder_key(resource: str, meta: ResourceMeta) -> tuple[str, int, UUID, str]:
    return (resource, meta.pid, meta.session_id, meta.role)


def acquire_resource(resource: str, meta: ResourceMeta, timeout: float = 0) -> BaseFileLock:
    """Acquire a file lock for a physical resource.

    Re-entrant for the same holder: if ``(pid, session_id, role)`` already
    holds *resource*, the acquisition increments a refcount and returns
    immediately without contending — enabling nested steps where a
    class-container step holds the lease while a method step re-acquires the
    same instrument.  N acquires from the same holder require N calls to
    :func:`release_resource`; the underlying ``fcntl.flock`` is released only
    when the refcount reaches zero.

    A *different* holder contends normally: ``timeout=0`` raises
    :exc:`ResourceInUse` immediately; ``timeout>0`` waits up to that many
    seconds; ``timeout=-1`` blocks indefinitely until the current holder
    releases.  Dead-holder recovery on the ``timeout=-1`` path requires no
    heartbeat watchdog because ``fcntl.flock`` auto-releases on process death
    (including SIGKILL) — the file-lock substrate handles it at the OS level.
    The server-path heartbeat/timeout split is a separate concern addressed in
    a later phase.

    Args:
        resource: Resource address (e.g. ``GPIB::16::INSTR``).
        meta: Metadata about the acquirer (written into lock file).
        timeout: Seconds to wait for a live holder.  ``0`` = fail immediately;
            positive = bounded wait; ``-1`` = wait forever.

    Returns:
        The held ``FileLock`` — caller must keep a reference and pass it to
        :func:`release_resource`.

    Raises:
        ResourceInUse: If the lock is held by a different holder and
            ``timeout`` expires before it is released.
    """
    key = _holder_key(resource, meta)

    with _registry_lock:
        if key in _registry:
            refcount, existing_lock = _registry[key]
            _registry[key] = (refcount + 1, existing_lock)
            return existing_lock

    lock_path = _lock_dir() / f"{_sanitize_resource(resource)}.lock"
    lock = FileLock(lock_path)
    try:
        lock.acquire(timeout=timeout)
    except Timeout:
        holder = lock_holder(resource)
        raise ResourceInUse(resource, holder) from None

    try:
        lock_path.write_text(meta.model_dump_json())
    except OSError:
        pass

    with _registry_lock:
        _registry[key] = (1, lock)
        _lock_to_key[id(lock)] = key

    return lock


def release_resource(resource: str, lock: BaseFileLock) -> None:
    """Release a resource lock.

    Decrements the re-entrancy refcount for the holder associated with *lock*.
    The underlying ``fcntl.flock`` is released only when the refcount reaches
    zero, that is, after as many :func:`release_resource` calls as there were
    :func:`acquire_resource` calls from the same holder.
    """
    should_release = False
    with _registry_lock:
        key = _lock_to_key.get(id(lock))
        if key is not None and key[0] == resource:
            refcount, _ = _registry[key]
            if refcount > 1:
                _registry[key] = (refcount - 1, lock)
            else:
                del _registry[key]
                del _lock_to_key[id(lock)]
                should_release = True
        else:
            should_release = True

    if should_release:
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
