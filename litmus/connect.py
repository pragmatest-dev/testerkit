"""Unified instrument connection API.

``litmus.connect()`` is the main entry point for non-pytest instrument access.
Scripts, Jupyter notebooks, the NiceGUI operator panel, and background
monitoring all use this to connect to a test station.

Usage::

    import litmus

    with litmus.connect("cell-7") as station:
        dmm = station.instrument("dmm")
        v = dmm.measure_voltage()

    # Or explicit lifecycle for UIs:
    station = litmus.connect("cell-7")
    station.start()
    dmm = station.instrument("dmm")
    station.release("dmm")
    station.stop()
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from litmus.data.channels.store import ChannelStore
from litmus.data.event_log import EventLog
from litmus.data.event_store import EventStore
from litmus.data.events import (
    InstrumentConfigure,
    SessionEnded,
    SessionStarted,
)
from litmus.instruments.models import InstrumentRecord
from litmus.instruments.pool import InstrumentPool
from litmus.schemas import StationConfig
from litmus.signals import deregister_cleanup, register_cleanup


class StationConnection:
    """Connection to a test station with instrument access and event logging.

    Owns: station config, EventLog, subscribers, instrument locks, proxying.
    Supports context manager (scripts) and explicit start/stop (UI).
    """

    def __init__(
        self,
        station_config: StationConfig,
        *,
        results_dir: Path | None = None,
        mock: bool = False,
    ) -> None:
        self._config = station_config
        self._results_dir = results_dir
        self._mock = mock
        self._session_id = uuid4()
        self._event_store: EventStore | None = None
        self._event_log: EventLog | None = None
        self._pool: InstrumentPool | None = None
        self._channel_store: ChannelStore | None = None
        self._started = False

    def start(self) -> None:
        """Create EventLog, emit SessionStarted."""
        if self._started:
            return

        self._event_store = EventStore(_results_dir=self._results_dir)
        self._event_log = self._event_store.get_event_log(self._session_id)

        # Create ChannelStore directly (not as EventLog subscriber)
        results_dir = self._event_store._results_dir
        self._channel_store = ChannelStore(
            results_dir / "channels", self._session_id, serve=True,
        )
        self._channel_store.open()

        self._pool = InstrumentPool(
            session_id=self._session_id,
            event_log=self._event_log,
            channel_store=self._channel_store,
            mock_all=self._mock,
            station_id=self._config.id,
        )

        # Register cleanup callback for SIGTERM/atexit
        cleanup_key = str(self._session_id)
        register_cleanup(cleanup_key, self._emergency_stop)

        self._event_log.emit(
            SessionStarted(
                session_id=self._session_id,
                station_id=self._config.id,
                station_name=self._config.name,
                station_type=self._config.station_type,
                station_location=self._config.location,
                dut_serial="",
                session_type="interactive",
                pid=os.getpid(),
            )
        )
        self._started = True

    def stop(self, outcome: str = "complete") -> None:
        """Release all instruments, emit SessionEnded, close EventLog."""
        if not self._started:
            return

        # Release all instruments
        if self._pool:
            self._pool.release_all()

        if self._event_log:
            self._event_log.emit(
                SessionEnded(
                    session_id=self._session_id,
                    outcome=outcome,
                )
            )

        if self._channel_store:
            self._channel_store.close()
            self._channel_store = None

        if self._event_store:
            self._event_store.close()
            self._event_store = None
        self._event_log = None

        deregister_cleanup(str(self._session_id))
        self._started = False

    def instrument(self, role: str, timeout: float = 0) -> Any:
        """Connect and lock a single instrument by role.

        Args:
            role: Instrument role name from station config.
            timeout: Seconds to wait for lock. 0 = fail immediately.

        Returns:
            Proxied driver instance.

        Raises:
            ResourceInUse: If the resource is locked by another process.
            KeyError: If the role is not in the station config.
        """
        if not self._started:
            self.start()

        assert self._pool is not None

        if role in self._pool.active:
            return self._pool.active[role]

        inst_configs = self._config.instruments or {}
        if role not in inst_configs:
            raise KeyError(
                f"Instrument role {role!r} not found in station {self._config.id!r}. "
                f"Available: {list(inst_configs)}"
            )

        inst_config = inst_configs[role]
        record = InstrumentRecord(
            role=role,
            instrument_id=role,
            resource=inst_config.resource or "",
            driver=inst_config.driver,
            catalog_ref=inst_config.catalog_ref,
            mocked=self._mock or inst_config.mock,
        )

        return self._pool.acquire(role, record, inst_config, timeout=timeout)

    def release(self, role: str) -> None:
        """Disconnect and unlock a single instrument.

        Emits InstrumentDisconnected, disconnects driver, releases lock.
        """
        if self._pool:
            self._pool.release(role)

    def configure(self, role: str, method: str, **parameters: Any) -> None:
        """Emit an InstrumentConfigure event for a UI-initiated operation.

        Use this for actions the UI performs that aren't driver method calls
        (e.g., starting continuous acquisition, changing display mode).

        Args:
            role: Instrument role (e.g., "scope").
            method: Descriptive name for the operation.
            **parameters: Key-value pairs describing the operation.
        """
        if self._event_log is None:
            return
        self._event_log.emit(InstrumentConfigure(
            session_id=self._session_id,
            instrument_role=role,
            method=method,
            parameters={k: v for k, v in parameters.items() if v is not None},
        ))

    def events(
        self,
        *,
        event_type: str | None = None,
        role: str | None = None,
    ) -> list[dict]:
        """Read events from this session's log.

        Args:
            event_type: Filter by event_type (e.g. "instrument.read").
            role: Filter by instrument role.

        Returns:
            List of event dicts, oldest first.
        """
        if self._event_store is None:
            return []
        return self._event_store.events(
            session_id=self._session_id,
            event_type=event_type,
            role=role,
        )

    def on_event(
        self,
        callback: Callable[[dict], None],
        *,
        event_type: str | None = None,
        role: str | None = None,
        since: datetime | None = None,
    ) -> Callable[[], None]:
        """Subscribe to events from this session.

        Replays matching events, then pushes new ones as they arrive.
        Returns an unsubscribe callable.
        """
        if self._event_store is None:
            raise RuntimeError("Station not started")
        return self._event_store.on_event(
            callback,
            event_type=event_type,
            role=role,
            session_id=self._session_id,
            since=since,
        )

    def observe(
        self,
        key: str,
        value: object,
        *,
        units: str | None = None,
        sample_interval: float | None = None,
    ) -> str:
        """Write an observation to the ChannelStore.

        Interactive equivalent of ``context.observe(key, value)`` in pytest.

        Args:
            key: Channel name (e.g. "scope.ch1_waveform", "temp_reading").
            value: Scalar or numeric array.
            units: Optional unit string.
            sample_interval: For array data, seconds between samples.

        Returns:
            ``channel://`` URI pointing to the stored data.

        Raises:
            RuntimeError: If station not started or ChannelStore unavailable.
        """
        if not self._started:
            self.start()
        if self._channel_store is None:
            raise RuntimeError("ChannelStore not available")
        return self._channel_store.write(
            key, value, units=units, sample_interval=sample_interval,
        )

    @property
    def instruments(self) -> dict[str, Any]:
        """Currently connected instruments by role."""
        if self._pool:
            return dict(self._pool.active)
        return {}

    @property
    def event_log(self) -> EventLog | None:
        return self._event_log

    @property
    def session_id(self) -> UUID:
        return self._session_id

    @property
    def config(self) -> StationConfig:
        return self._config

    @property
    def event_store(self) -> EventStore | None:
        return self._event_store

    @property
    def channel_store(self) -> ChannelStore | None:
        return self._channel_store

    def _emergency_stop(self) -> None:
        """Best-effort cleanup on SIGTERM/atexit."""
        try:
            self.stop(outcome="interrupted")
        except Exception:
            pass

    def __enter__(self) -> StationConnection:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        outcome = "complete" if exc_type is None else "error"
        self.stop(outcome=outcome)


def _find_project_config() -> tuple[Path, Any] | None:
    """Find litmus.yaml in CWD ancestors. Returns (project_root, ProjectConfig) or None."""
    from litmus.store import load_project

    current = Path.cwd()
    while current != current.parent:
        candidate = current / "litmus.yaml"
        if candidate.exists():
            try:
                return current, load_project(candidate)
            except (ValueError, OSError) as exc:
                import warnings

                warnings.warn(f"Failed to load {candidate}: {exc}", stacklevel=2)
                return None
        current = current.parent
    return None


def connect(
    station: str | None = None,
    *,
    results_dir: Path | None = None,
    mock: bool = False,
) -> StationConnection:
    """Connect to a test station. The main entry point for non-pytest usage.

    Args:
        station: Station ID. If None, uses ``default_station`` from ``litmus.yaml``.
        results_dir: Where to write events. Falls back to litmus.yaml → LITMUS_HOME.
        mock: Use mock instruments.

    Returns:
        A ``StationConnection`` (usable as context manager or via start/stop).
    """
    from litmus.store import find_station_config

    if station is None:
        station = _default_station_id()
        if station is None:
            raise ValueError(
                "No station specified and no default_station in litmus.yaml. "
                "Pass a station ID: litmus.connect('my-station')"
            )

    config = find_station_config(station)
    return StationConnection(config, results_dir=results_dir, mock=mock)


def _default_station_id() -> str | None:
    """Read default_station from litmus.yaml in CWD ancestors."""
    found = _find_project_config()
    if found:
        _, project = found
        return getattr(project, "default_station", None)
    return None
