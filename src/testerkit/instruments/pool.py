"""Internal instrument lifecycle pool.

Consolidates lock → connect → verify → wrap → disconnect logic shared
by the pytest plugin's ``instruments`` fixture and ``testerkit.connect()``.
This is NOT a user-facing API.
"""

from __future__ import annotations

import os
import time
import warnings
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from filelock._api import BaseFileLock

from testerkit.data.event_log import EventLog
from testerkit.data.events import (
    InstrumentConnected,
    InstrumentDisconnected,
    InstrumentReleased,
    InstrumentReserved,
)
from testerkit.instruments.lifecycle import (
    disconnect,
    load_and_connect,
    load_driver_class,
    verify_and_wrap,
)
from testerkit.instruments.locks import (
    ResourceMeta,
    acquire_resource,
    release_resource,
)
from testerkit.instruments.observer import DriverObserver, InstrumentEventBuilder
from testerkit.instruments.observers import detect_protocol, get_observer_class
from testerkit.models.instrument import InstrumentRecord
from testerkit.models.station import StationInstrumentConfig


class InstrumentPool:
    """Manages instrument lifecycle: lock, connect, verify, wrap, disconnect.

    Used internally by both the pytest plugin and StationConnection.
    """

    def __init__(
        self,
        session_id: UUID | None,
        event_log: EventLog | None,
        channel_store: Any | None,
        mock_all: bool = False,
        station_id: str = "",
        run_id: UUID | None = None,
    ) -> None:
        self._session_id = session_id or UUID(int=0)
        self._event_log = event_log
        self._channel_store = channel_store
        self._mock_all = mock_all
        self._station_id = station_id
        self._run_id = run_id
        self._active: dict[str, Any] = {}
        self._records: dict[str, InstrumentRecord] = {}
        self._locks: dict[str, BaseFileLock] = {}
        self._remote_proxies: dict[str, Any] = {}

    @property
    def active(self) -> dict[str, Any]:
        return self._active

    @property
    def records(self) -> dict[str, InstrumentRecord]:
        return self._records

    def connect(
        self,
        role: str,
        record: InstrumentRecord,
        inst_config: StationInstrumentConfig | None = None,
    ) -> Any:
        """Load → connect → verify → wrap → emit InstrumentConnected. No lock acquired.

        Idempotent: if the role is already active, returns the existing driver.
        If the role is served by a remote instrument server, delegates to
        ``_acquire_remote`` (proxy, no local connection, no lock).
        """
        if role in self._active:
            return self._active[role]

        shared_roles = os.environ.get("_TESTERKIT_SHARED_ROLES", "")
        server_addr = os.environ.get("_TESTERKIT_INSTRUMENT_SERVER", "")
        if role in shared_roles.split(",") and server_addr:
            return self._acquire_remote(role, record, server_addr)

        use_mock = self._mock_all or record.mocked
        record.mocked = use_mock
        mock_config = (inst_config.mock_config if inst_config else None) or {}

        driver_class = _resolve_driver_class(record) if not use_mock else None
        driver = load_and_connect(
            record,
            mock=use_mock,
            mock_config=mock_config,
            driver_class=driver_class,
        )

        observer = self._build_observer(role, record, inst_config, driver, driver_class)
        inst = verify_and_wrap(
            driver,
            role,
            record,
            self._event_log,
            self._session_id,
            observer=observer,
        )

        self._active[role] = inst
        self._records[role] = record
        self._emit_connected(role, record)
        return inst

    def reserve(
        self,
        role: str,
        timeout: float = 0,
        *,
        step_index: int | None = None,
        step_retry: int | None = None,
    ) -> None:
        """Acquire the exclusive file lock for an already-attached role.

        Re-entrant for the same ``(pid, session_id, role)`` holder: increments
        the refcount without contending. Each call requires a matching
        ``release_reservation``.

        For remote/shared roles the server owns arbitration; this is a no-op
        at the client (server leases are Phase 2b). For mocked or resource-less
        roles, no lock is taken but an event is still emitted.

        Raises:
            ResourceInUse: If the resource is held by a different process and
                ``timeout`` expires (``0`` = fail immediately; ``-1`` = wait
                forever for a live holder).
        """
        shared_roles = os.environ.get("_TESTERKIT_SHARED_ROLES", "")
        server_addr = os.environ.get("_TESTERKIT_INSTRUMENT_SERVER", "")
        if role in shared_roles.split(",") and server_addr:
            raw = self._remote_proxies.get(role)
            if raw is not None:
                t0 = time.monotonic()
                raw.reserve(timeout)
                waited_ms = (time.monotonic() - t0) * 1000.0
                record = self._records.get(role)
                if self._event_log is not None and record is not None:
                    self._event_log.emit(
                        InstrumentReserved(
                            session_id=self._session_id,
                            run_id=self._run_id,
                            role=role,
                            instrument_id=record.instrument_id,
                            resource=record.resource,
                            waited_ms=waited_ms,
                            step_index=step_index,
                            step_retry=step_retry,
                        )
                    )
            return

        record = self._records.get(role)
        if record is None:
            return

        if record.mocked or not record.resource:
            if self._event_log is not None:
                self._event_log.emit(
                    InstrumentReserved(
                        session_id=self._session_id,
                        run_id=self._run_id,
                        role=role,
                        instrument_id=record.instrument_id,
                        resource=record.resource,
                        waited_ms=0.0,
                        step_index=step_index,
                        step_retry=step_retry,
                    )
                )
            return

        meta = ResourceMeta(
            pid=os.getpid(),
            session_id=self._session_id,
            station_id=self._station_id,
            role=role,
            acquired_at=datetime.now(UTC),
        )
        t0 = time.monotonic()
        lock = acquire_resource(record.resource, meta, timeout=timeout)
        waited_ms = (time.monotonic() - t0) * 1000.0
        self._locks[role] = lock
        if self._event_log is not None:
            self._event_log.emit(
                InstrumentReserved(
                    session_id=self._session_id,
                    run_id=self._run_id,
                    role=role,
                    instrument_id=record.instrument_id,
                    resource=record.resource,
                    waited_ms=waited_ms,
                    step_index=step_index,
                    step_retry=step_retry,
                )
            )

    def release_reservation(
        self,
        role: str,
        *,
        step_index: int | None = None,
        step_retry: int | None = None,
    ) -> None:
        """Release one refcount of the file lock, leaving the driver attached.

        The underlying flock is freed only when all outstanding ``reserve``
        refcounts have been released. A no-op if no reservation is held.
        """
        shared_roles = os.environ.get("_TESTERKIT_SHARED_ROLES", "")
        server_addr = os.environ.get("_TESTERKIT_INSTRUMENT_SERVER", "")
        if role in shared_roles.split(",") and server_addr:
            raw = self._remote_proxies.get(role)
            if raw is not None:
                raw.release_reservation()
                record = self._records.get(role)
                if self._event_log is not None and record is not None:
                    self._event_log.emit(
                        InstrumentReleased(
                            session_id=self._session_id,
                            run_id=self._run_id,
                            role=role,
                            instrument_id=record.instrument_id,
                            resource=record.resource,
                            step_index=step_index,
                            step_retry=step_retry,
                        )
                    )
            return

        record = self._records.get(role)
        if record is None:
            return

        if record.mocked or not record.resource:
            if self._event_log is not None:
                self._event_log.emit(
                    InstrumentReleased(
                        session_id=self._session_id,
                        run_id=self._run_id,
                        role=role,
                        instrument_id=record.instrument_id,
                        resource=record.resource,
                        step_index=step_index,
                        step_retry=step_retry,
                    )
                )
            return

        lock = self._locks.get(role)
        if lock is None:
            return
        release_resource(record.resource, lock)
        if self._event_log is not None:
            self._event_log.emit(
                InstrumentReleased(
                    session_id=self._session_id,
                    run_id=self._run_id,
                    role=role,
                    instrument_id=record.instrument_id,
                    resource=record.resource,
                    step_index=step_index,
                    step_retry=step_retry,
                )
            )
        if not lock.is_locked:
            del self._locks[role]

    def acquire(
        self,
        role: str,
        record: InstrumentRecord,
        inst_config: StationInstrumentConfig | None = None,
        timeout: float = 0,
    ) -> Any:
        """Back-compat composite: connect + reserve.

        Existing callers (pytest fixture, route_manager) continue working
        unchanged. If ``reserve`` fails, cleans up the connection so no resources
        remain held.
        """
        inst = self.connect(role, record, inst_config)
        try:
            self.reserve(role, timeout=timeout)
        except BaseException:
            self.disconnect(role)
            raise
        return inst

    def _acquire_remote(
        self,
        role: str,
        record: InstrumentRecord,
        server_addr: str,
    ) -> Any:
        """Acquire a remote instrument proxy from the instrument server.

        Skips file locks (server handles serialization) and local connection.
        Still wraps in InstrumentProxy if event log is active.
        """
        from testerkit.instruments.server import RemoteInstrumentProxy, connect_to_server

        address = connect_to_server(server_addr)
        proxy = RemoteInstrumentProxy(address, role)
        self._remote_proxies[role] = proxy

        if self._event_log is not None:
            observer = self._build_observer(role, record, None, proxy)
            if observer is not None:
                from testerkit.instruments.proxy import InstrumentProxy

                proxy = InstrumentProxy(proxy, role, observer)

        self._active[role] = proxy
        self._records[role] = record
        self._emit_connected(role, record)
        return proxy

    def _build_observer(
        self,
        role: str,
        record: InstrumentRecord,
        inst_config: StationInstrumentConfig | None,
        driver: Any,
        driver_class: type | None = None,
    ) -> DriverObserver | None:
        """Construct the appropriate observer, or None if no event log."""
        if self._event_log is None:
            return None

        channel_overrides = (inst_config.channels if inst_config else None) or {}
        protocol = detect_protocol(driver_class) if driver_class else "generic"
        observer_cls = get_observer_class(protocol)

        emitter = InstrumentEventBuilder(
            event_log=self._event_log,
            session_id=self._session_id,
            role=role,
            run_id=self._run_id,
            resource=record.resource,
            channel_store=self._channel_store,
        )
        return observer_cls(
            driver_class or type(driver),
            role,
            emitter,
            yaml_overrides=channel_overrides or None,
            driver_instance=driver,
        )

    def disconnect(self, role: str) -> None:
        """Emit InstrumentDisconnected → disconnect → drain all lock refcounts."""
        inst = self._active.pop(role, None)
        record = self._records.pop(role, None)
        self._remote_proxies.pop(role, None)

        if inst is not None:
            if self._event_log and record:
                self._event_log.emit(
                    InstrumentDisconnected(
                        session_id=self._session_id,
                        run_id=self._run_id,
                        role=role,
                        instrument_id=record.instrument_id,
                    )
                )
            disconnect(inst, role)

        lock = self._locks.pop(role, None)
        if lock is not None and record and record.resource:
            while lock.is_locked:
                release_resource(record.resource, lock)

    def disconnect_all(self) -> None:
        """Disconnect all instruments in reverse acquisition order."""
        for role in reversed(list(self._active)):
            self.disconnect(role)

    def _emit_connected(self, role: str, record: InstrumentRecord) -> None:
        if not self._event_log:
            return
        self._event_log.emit(
            InstrumentConnected(
                session_id=self._session_id,
                run_id=self._run_id,
                role=role,
                instrument_id=record.instrument_id,
                driver=record.driver,
                resource=record.resource,
                protocol=record.protocol,
                manufacturer=record.info.manufacturer if record.info else None,
                model=record.info.model if record.info else None,
                serial=record.info.serial if record.info else None,
                firmware=record.info.firmware if record.info else None,
                cal_due=(
                    record.calibration.due_date.isoformat()
                    if record.calibration and record.calibration.due_date
                    else None
                ),
                cal_last=(
                    record.calibration.last_cal.isoformat()
                    if record.calibration and record.calibration.last_cal
                    else None
                ),
                cal_certificate=(record.calibration.certificate if record.calibration else None),
                cal_lab=record.calibration.lab if record.calibration else None,
                mocked=record.mocked,
            )
        )


def _resolve_driver_class(record: InstrumentRecord) -> type | None:
    """Resolve driver class from record, trying explicit driver then catalog_ref."""
    # 1. Explicit driver path
    if record.driver:
        cls = load_driver_class(record.driver)
        if cls is not None:
            return cls
        warnings.warn(
            f"{record.role}: could not load driver class {record.driver!r}",
            stacklevel=3,
        )

    # 2. Catalog ref → catalog entry → driver field
    if record.catalog_ref:
        return _resolve_from_catalog(record.catalog_ref)

    return None


def _resolve_from_catalog(catalog_ref: str) -> type | None:
    """Load catalog entry and resolve its driver class, if any."""
    try:
        from testerkit.store import resolve_catalog_ref

        entry = resolve_catalog_ref(catalog_ref)
        if entry and entry.driver:
            return load_driver_class(entry.driver)
    except (ImportError, OSError, ValueError, KeyError):
        pass
    return None
