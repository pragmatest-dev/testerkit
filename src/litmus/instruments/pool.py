"""Internal instrument lifecycle pool.

Consolidates lock → connect → verify → wrap → disconnect logic shared
by the pytest plugin's ``instruments`` fixture and ``litmus.connect()``.
This is NOT a user-facing API.
"""

from __future__ import annotations

import os
import warnings
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from filelock._api import BaseFileLock

from litmus.data.event_log import EventLog
from litmus.data.events import InstrumentConnected, InstrumentDisconnected
from litmus.instruments.lifecycle import (
    disconnect,
    load_and_connect,
    load_driver_class,
    verify_and_wrap,
)
from litmus.instruments.locks import (
    ResourceMeta,
    acquire_resource,
    release_resource,
)
from litmus.instruments.observer import DriverObserver, EventEmitter
from litmus.instruments.observers import detect_protocol, get_observer_class
from litmus.models.instrument import InstrumentRecord
from litmus.models.station import StationInstrumentConfig


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

    @property
    def active(self) -> dict[str, Any]:
        return self._active

    @property
    def records(self) -> dict[str, InstrumentRecord]:
        return self._records

    def acquire(
        self,
        role: str,
        record: InstrumentRecord,
        inst_config: StationInstrumentConfig | None = None,
        timeout: float = 0,
    ) -> Any:
        """Lock → load → connect → verify → wrap → emit InstrumentConnected.

        If the role is served by a remote instrument server (indicated by
        ``LITMUS_SHARED_ROLES`` and ``LITMUS_INSTRUMENT_SERVER`` env vars),
        returns a ``RemoteInstrumentProxy`` instead of connecting locally.

        Returns the proxied driver instance.
        """
        # Check if this role is served remotely by the instrument server
        shared_roles = os.environ.get("LITMUS_SHARED_ROLES", "")
        server_addr = os.environ.get("LITMUS_INSTRUMENT_SERVER", "")
        if role in shared_roles.split(",") and server_addr:
            return self._acquire_remote(role, record, server_addr)
        use_mock = self._mock_all or record.mocked
        record.mocked = use_mock
        mock_config = inst_config.mock_config if inst_config and inst_config.mock_config else {}

        # Acquire resource lock (skip for mocks with no real resource)
        lock: BaseFileLock | None = None
        if record.resource and not record.mocked:
            meta = ResourceMeta(
                pid=os.getpid(),
                session_id=self._session_id,
                station_id=self._station_id,
                role=role,
                acquired_at=datetime.now(UTC),
            )
            lock = acquire_resource(record.resource, meta, timeout=timeout)

        try:
            # Resolve driver class once — used for both connection and observer
            driver_class = _resolve_driver_class(record) if not use_mock else None
            driver = load_and_connect(
                record,
                mock=use_mock,
                mock_config=mock_config,
                driver_class=driver_class,
            )

            # Build observer from driver class + YAML overrides
            observer = self._build_observer(
                role,
                record,
                inst_config,
                driver,
                driver_class,
            )

            inst = verify_and_wrap(
                driver,
                role,
                record,
                self._event_log,
                self._session_id,
                observer=observer,
            )
        except BaseException:
            if lock is not None:
                release_resource(record.resource, lock)
            raise

        self._active[role] = inst
        self._records[role] = record
        if lock is not None:
            self._locks[role] = lock

        self._emit_connected(role, record)
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
        from litmus.instruments.server import RemoteInstrumentProxy, connect_to_server

        address = connect_to_server(server_addr)
        proxy = RemoteInstrumentProxy(address, role)

        # Wrap in observer proxy if event log is active
        if self._event_log is not None:
            observer = self._build_observer(role, record, None, proxy)
            if observer is not None:
                from litmus.instruments.proxy import InstrumentProxy

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

        channel_overrides = inst_config.channels if inst_config else {}
        protocol = detect_protocol(driver_class) if driver_class else "generic"
        observer_cls = get_observer_class(protocol)

        emitter = EventEmitter(
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

    def release(self, role: str) -> None:
        """Emit InstrumentDisconnected → disconnect → release lock."""
        inst = self._active.pop(role, None)
        record = self._records.pop(role, None)

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
        if lock is not None and record:
            release_resource(record.resource, lock)

    def release_all(self) -> None:
        """Release all instruments in reverse acquisition order."""
        for role in list(reversed(list(self._active))):
            self.release(role)

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
        from litmus.store import resolve_catalog_ref

        entry = resolve_catalog_ref(catalog_ref)
        if entry and entry.driver:
            return load_driver_class(entry.driver)
    except (ImportError, OSError, ValueError, KeyError):
        pass
    return None
