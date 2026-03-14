"""Shared instrument access for multi-DUT testing.

Provides two modes for sharing a single physical instrument across slots:

- **SharedInstrumentProvider** (reconnect-safe): Per-measurement lifecycle.
  Lock → connect → yield → disconnect → unlock. Used in subprocess mode.
- **SharedInstrumentHandle** (persistent): Session-scoped connection with
  mutex-protected access. Used in thread mode.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from litmus.instruments.lifecycle import disconnect, load_and_connect
from litmus.instruments.locks import ResourceMeta, acquire_resource, release_resource
from litmus.instruments.models import InstrumentRecord
from litmus.schemas import StationInstrumentConfig

logger = logging.getLogger(__name__)


class SharedInstrumentProvider:
    """On-demand lock → connect → disconnect → release for shared instruments.

    Used in subprocess mode where each slot runs in its own process.
    Each measurement acquires the instrument exclusively, connects,
    performs the operation, disconnects, and releases the lock.

    Mock instruments skip locking and real connections.

    Args:
        role: Instrument role name (e.g., "dmm").
        record: Instrument record with driver, resource, calibration info.
        inst_config: Station instrument config (for mock_config).
        mock_all: Whether all instruments are mocked.
        session_id: Current session UUID.
        station_id: Station identifier.
    """

    def __init__(
        self,
        role: str,
        record: InstrumentRecord,
        inst_config: StationInstrumentConfig | None = None,
        mock_all: bool = False,
        session_id: UUID | None = None,
        station_id: str = "",
    ) -> None:
        self._role = role
        self._record = record
        self._inst_config = inst_config
        self._mock = mock_all or record.mocked
        self._mock_config = inst_config.mock_config if inst_config else {}
        self._session_id = session_id or uuid4()
        self._station_id = station_id

    @property
    def role(self) -> str:
        return self._role

    @contextmanager
    def connection(self, timeout: float = 30) -> Generator[Any, None, None]:
        """Acquire lock, connect, yield driver, disconnect, release.

        For mock instruments, skips locking and yields a mock directly.

        Args:
            timeout: Seconds to wait for the resource lock.

        Yields:
            Connected driver instance.
        """
        if self._mock:
            driver = load_and_connect(
                self._record, mock=True, mock_config=self._mock_config,
            )
            yield driver
            return

        # Acquire resource lock
        meta = ResourceMeta(
            pid=os.getpid(),
            session_id=self._session_id,
            station_id=self._station_id,
            role=self._role,
            acquired_at=datetime.now(UTC),
        )
        lock = acquire_resource(
            self._record.resource or self._role, meta, timeout=timeout,
        )

        try:
            driver = load_and_connect(
                self._record, mock=False, mock_config=self._mock_config,
            )
            try:
                yield driver
            finally:
                disconnect(driver, self._role)
        finally:
            release_resource(self._record.resource or self._role, lock)


class SharedInstrumentHandle:
    """Thread-safe wrapper around a persistent shared instrument.

    One driver instance, connected at session start.

    Two modes of access:
    - **Serialized** (``concurrent=False``, default): Method calls go through
      a ``threading.Lock`` so only one slot thread uses the instrument at a
      time. Use for measurement instruments (DMM, PSU) where only one
      measurement can happen at once.
    - **Concurrent** (``concurrent=True``): No mutex. Multiple threads access
      the driver simultaneously. Use for **switches / relay matrices** where
      each slot controls different channels and the hardware supports
      concurrent channel operations. Logical conflicts are handled by
      RouteManager's conflict detection, not by locking.

    No file locks needed — threads share memory within one process.

    Args:
        role: Instrument role name.
        driver: Already-connected driver instance.
        lock: Threading lock for serialized access.
        concurrent: If True, skip mutex and allow simultaneous access.
            Use for switches where each slot operates on different channels.
    """

    def __init__(
        self,
        role: str,
        driver: Any,
        lock: threading.Lock | None = None,
        *,
        concurrent: bool = False,
    ) -> None:
        self._role = role
        self._driver = driver
        self._lock = lock or threading.Lock()
        self._concurrent = concurrent

    @property
    def role(self) -> str:
        return self._role

    @property
    def driver(self) -> Any:
        """The underlying driver instance (use ``acquire()`` for safe access)."""
        return self._driver

    @property
    def concurrent(self) -> bool:
        """True if this handle allows simultaneous access (no mutex)."""
        return self._concurrent

    @contextmanager
    def acquire(self, timeout: float = 30) -> Generator[Any, None, None]:
        """Acquire access to the shared driver, yield it, then release.

        For concurrent handles (switches), yields the driver immediately
        without locking. For serialized handles, acquires the mutex first.

        Args:
            timeout: Seconds to wait for the lock (ignored if concurrent).

        Yields:
            The shared driver instance.

        Raises:
            TimeoutError: If the lock cannot be acquired within timeout
                (only for serialized handles).
        """
        if self._concurrent:
            yield self._driver
            return

        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire shared instrument '{self._role}' "
                f"within {timeout}s — another slot may be holding it"
            )
        try:
            yield self._driver
        finally:
            self._lock.release()
