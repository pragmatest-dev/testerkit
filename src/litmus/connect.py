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

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from litmus.data.channels.store import ChannelStore
from litmus.data.event_log import EventLog
from litmus.data.event_store import EventStore
from litmus.data.events import InstrumentConfigure
from litmus.execution.session_scope import SessionScope, build_session_started, open_session
from litmus.instruments.pool import InstrumentPool
from litmus.models.data_options import SessionOptions, StreamTuning
from litmus.models.instrument import InstrumentRecord
from litmus.models.station import StationConfig
from litmus.signals import deregister_cleanup, register_cleanup

# Interactive sessions outlive long human pauses at the bench, so the owner
# declares a patient lease floor — never shorter than this, though a project
# that configured an even longer ``session.idle_lease_seconds`` keeps it.
_INTERACTIVE_IDLE_LEASE_SECONDS = 3600.0


class StationConnection:
    """Connection to a test station with instrument access and event logging.

    Owns: station config, EventLog, subscribers, instrument locks, proxying.
    Supports context manager (scripts) and explicit start/stop (UI).
    """

    def __init__(
        self,
        station_config: StationConfig,
        *,
        data_dir: Path | None = None,
        mock: bool = False,
    ) -> None:
        self._config = station_config
        self._data_dir = data_dir
        self._mock = mock
        self._session_id = uuid4()
        self._scope: SessionScope | None = None
        self._event_store: EventStore | None = None
        self._event_log: EventLog | None = None
        self._pool: InstrumentPool | None = None
        self._channel_store: ChannelStore | None = None
        self._sync_point: Any = None
        self._instrument_server: Any = None
        self._started = False

    def start(self) -> None:
        """Open the session (emit SessionStarted, wire ContextVars) + connect capabilities."""
        if self._started:
            return

        # Open the producer session via the shared primitive: it creates the
        # EventStore + EventLog, wires the EventStore ContextVar, and emits
        # SessionStarted. connect always owns its EventStore (reuse_existing=False).
        # Resolve the will from the project's session options (litmus.yaml
        # ``session:``), then apply the interactive lease floor — the owner-side
        # per-session_type override the design routes through the caller.
        _proj = _find_project_config()
        _base_session = _proj[1].session if _proj else SessionOptions()
        _session_opts = _base_session.model_copy(
            update={
                "idle_lease_seconds": max(
                    _base_session.idle_lease_seconds, _INTERACTIVE_IDLE_LEASE_SECONDS
                )
            }
        )
        started = build_session_started(
            self._config,
            session_id=self._session_id,
            session_type="interactive",
            session_options=_session_opts,
        )
        self._scope = open_session(
            started,
            session_id=self._session_id,
            data_dir=self._data_dir,
            reuse_existing=False,
            emit_lifecycle=True,
        )
        self._event_store = self._scope.event_store
        self._event_log = self._scope.event_log

        # ChannelStore is constructed now but opens lazily on first channel write
        # (no daemon spin for a zero-channel session). Pass event_log so it can
        # emit ChannelStarted / ChannelEnded. Channel data options come from the
        # project config (litmus.yaml channels:), not the station config; absent →
        # ChannelOptions defaults.
        _stream_tuning = _proj[1].stream if _proj else StreamTuning()
        self._channel_store = ChannelStore(
            self._event_store._data_dir,
            self._session_id,
            options=_proj[1].channels if _proj else None,
            serve=True,
            event_log=self._event_log,
            checkpoint_cadence=_stream_tuning.resolve_cadence(_session_opts.idle_lease_seconds),
        )

        # Wire the ChannelStore ContextVar (open_session wired the EventStore) so
        # module-level surfaces (``litmus.channels.stream``, ``litmus.files.write``)
        # resolve to this session's stores. Token-managed via the scope so a nested
        # connect()/session restores the outer binding on close.
        self._scope.attach_channel_store(self._channel_store)

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

        # Set up sync point if in multi-slot worker mode
        from litmus.execution.sync import get_sync

        self._sync_point = get_sync(self._event_store)
        self._started = True

    @property
    def instrument_server_address(self) -> str | None:
        """Address of the instrument server, if running."""
        if self._instrument_server is not None:
            return self._instrument_server.address_str
        return None

    def start_instrument_server(
        self,
        roles: set[str] | None = None,
    ) -> str:
        """Start an instrument server for shared instruments.

        Connects the specified instruments (or all instruments if no roles
        given) and exposes them via IPC. Workers can use the returned
        address to get remote proxies.

        Args:
            roles: Instrument roles to serve. If None, serves all instruments.

        Returns:
            Server address as ``host:port`` string.
        """
        if self._instrument_server is not None:
            return self._instrument_server.address_str

        if not self._started:
            self.start()

        assert self._pool is not None

        from litmus.instruments.server import InstrumentServer

        inst_configs = self._config.instruments or {}
        serve_roles = roles if roles is not None else set(inst_configs.keys())

        if not serve_roles:
            raise ValueError("No instruments to serve")

        # Connect instruments if not already connected
        drivers: dict[str, Any] = {}
        resources: dict[str, str] = {}
        concurrent_roles: set[str] = set()
        for role in serve_roles:
            inst = self.instrument(role)
            # Unwrap proxy to get the raw driver for the server
            raw = getattr(inst, "_driver", inst)
            drivers[role] = raw
            cfg = inst_configs.get(role)
            if cfg:
                if cfg.resource:
                    resources[role] = cfg.resource
                if cfg.type == "switch":
                    concurrent_roles.add(role)

        self._instrument_server = InstrumentServer(
            drivers,
            resources=resources,
            concurrent_roles=concurrent_roles,
        )
        self._instrument_server.start()
        return self._instrument_server.address_str

    def stop(self, outcome: str = "passed") -> None:
        """Release instruments, then close the session (SessionEnded + store teardown)."""
        if not self._started:
            return

        # Stop instrument server before releasing instruments
        if self._instrument_server is not None:
            self._instrument_server.stop(force=True)
            self._instrument_server = None

        # Release all instruments
        if self._pool:
            self._pool.release_all()

        # End the session via the primitive: emit SessionEnded (best-effort fast-path
        # — connect is the sole producer of its session), then close stores. The
        # ChannelStore closes BEFORE the event log so its subscribers flush first.
        if self._scope is not None:
            self._scope.emit_ended()

        if self._channel_store:
            self._channel_store.close()
            self._channel_store = None

        self._sync_point = None

        if self._scope is not None:
            # Resets both store ContextVar tokens (restoring any outer session's
            # bindings) + closes the owned EventStore.
            self._scope.close_stores()
            self._scope = None
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
        self._event_log.emit(
            InstrumentConfigure(
                session_id=self._session_id,
                instrument_role=role,
                method=method,
                parameters={k: v for k, v in parameters.items() if v is not None},
            )
        )

    def events(
        self,
        *,
        event_type: str | None = None,
        role: str | None = None,
    ) -> list[dict]:
        """Read events from this session's log.

        Args:
            event_type: Filter by event_type (e.g. "channel.started").
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
        unit: str | None = None,
        sample_interval: float | None = None,
    ) -> str:
        """Write an observation to the ChannelStore.

        Interactive equivalent of ``context.observe(key, value)`` in pytest.

        Args:
            key: Channel name (e.g. "scope.ch1_waveform", "temp_reading").
            value: Scalar or numeric array.
            unit: Optional unit string.
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
            key,
            value,
            unit=unit,
            sample_interval=sample_interval,
        )

    def sync(self, name: str, timeout: float | None = None) -> None:
        """Wait at a named sync point (multi-UUT coordination).

        In single-slot mode (no _LITMUS_SLOT_ID), returns immediately.
        In multi-slot mode, blocks until all slots arrive at this point.

        Args:
            name: Sync point name (e.g., "thermal_soak").
            timeout: Max seconds to wait. None = wait forever.

        Raises:
            SyncError: If timeout expires before all slots arrive.
        """
        if self._sync_point is None:
            return  # Single-slot, no sync needed
        self._sync_point.wait(name, timeout=timeout)

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
        """Best-effort cleanup on SIGTERM/atexit.

        ``stop()`` runs the full cleanup chain (instruments to safe
        state, channel/event store close, parquet finalize) — the
        TestStand definition of TERMINATED. We only fall back to
        ABORTED if ``stop()`` itself blows up partway, signaling the
        rig is in an unknown state.
        """
        try:
            self.stop(outcome="terminated")
        except Exception:
            try:
                self.stop(outcome="aborted")
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
        # Operator-initiated stops (Ctrl-C, ``kill <pid>``, pytest's
        # KeyboardInterrupt → SystemExit) reach the context manager
        # via this exit path, which means we ARE running cleanup
        # (``stop()`` below). That's TestStand-Terminated, not
        # Errored, and not Aborted.
        if exc_type is None:
            outcome = "passed"
        elif issubclass(exc_type, KeyboardInterrupt | SystemExit):
            outcome = "terminated"
        else:
            outcome = "errored"
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
    data_dir: Path | None = None,
    mock: bool = False,
) -> StationConnection:
    """Connect to a test station. The main entry point for non-pytest usage.

    Args:
        station: Station ID. If None, uses ``default_station`` from ``litmus.yaml``.
        data_dir: Where to write events. Falls back to litmus.yaml → LITMUS_HOME.
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
    return StationConnection(config, data_dir=data_dir, mock=mock)


def _default_station_id() -> str | None:
    """Read default_station from litmus.yaml in CWD ancestors."""
    found = _find_project_config()
    if found:
        _, project = found
        return getattr(project, "default_station", None)
    return None
