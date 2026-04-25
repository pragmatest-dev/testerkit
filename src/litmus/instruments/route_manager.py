"""Route manager for switched signal routing.

Manages the lifecycle of switch routes: lock acquisition (instrument
then switch), channel closure, settling time, conflict detection,
and orderly teardown. Both the ``pins[]`` transparent pattern and
the ``routes.for_pin()`` explicit pattern share this engine.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from filelock._api import BaseFileLock

from litmus.config.test_config import FixtureConnection, SwitchRoute
from litmus.instruments.locks import ResourceMeta, acquire_resource
from litmus.instruments.switch import SwitchDriver

logger = logging.getLogger(__name__)


class RouteConflictError(Exception):
    """Raised when a route activation would conflict with an active route."""


_SWITCH_METHODS = ("close_channels", "open_channels", "open_all")


class RouteManager:
    """Manages switch route lifecycle, locking, and conflict detection.

    Built at session start from fixture connections that have routes.
    Holds locks and active channel state for the duration of the session.

    Lock ordering: instrument resource → switch resource (always).
    This prevents deadlocks when multiple processes share instruments.

    Args:
        connections: All fixture connections (keyed by connection name).
        instruments: Connected instrument instances (keyed by role).
        session_id: Current session UUID (for lock metadata).
        station_id: Station identifier (for lock metadata).
        event_log: Optional event log for emitting route events.
    """

    def __init__(
        self,
        connections: dict[str, FixtureConnection],
        instruments: dict[str, Any],
        session_id: UUID | None = None,
        station_id: str = "",
        event_log: Any = None,
    ) -> None:
        self._connections = connections
        self._instruments = instruments
        self._session_id = session_id
        self._station_id = station_id
        self._event_log = event_log

        # Active state
        self._active_routes: dict[str, SwitchRoute] = {}  # connection_name → active route
        self._held_locks: dict[str, BaseFileLock] = {}  # resource_key → held lock
        self._channel_owners: dict[tuple[str, str], str] = {}  # (switch_role, channel) → connection
        # Build reverse lookup: dut_pin → connection_name (for for_pin)
        self._pin_to_connection: dict[str, str] = {}
        # Build conflict model: instrument_channel → list of connection names
        self._instrument_channel_map: dict[tuple[str, str | None], list[str]] = {}

        for connection_name, connection in connections.items():
            if connection.route is not None:
                if connection.dut_pin:
                    self._pin_to_connection[connection.dut_pin] = connection_name
                key = (connection.instrument, connection.instrument_channel)
                self._instrument_channel_map.setdefault(key, []).append(connection_name)

    @property
    def has_routes(self) -> bool:
        """True if any fixture connections have switch routes."""
        return any(c.route is not None for c in self._connections.values())

    @property
    def active_routes(self) -> dict[str, SwitchRoute]:
        """Currently active routes (connection_name → SwitchRoute)."""
        return dict(self._active_routes)

    def activate(self, connection_name: str) -> None:
        """Activate a switch route for a fixture connection.

        Acquires locks (instrument → switch), closes channels, waits
        for settling. No-op if the route is already active.

        Raises:
            KeyError: If connection not found or has no route.
            RouteConflictError: If activation would conflict with active routes.
        """
        if connection_name in self._active_routes:
            return  # Already active, no-op

        connection = self._connections.get(connection_name)
        if connection is None:
            raise KeyError(f"Fixture connection '{connection_name}' not found")
        route = connection.route
        if route is None:
            raise KeyError(f"Fixture connection '{connection_name}' has no switch route")

        # Check for conflicts
        self._check_conflicts(connection_name, connection, route)

        # Resolve switch instrument
        switch = self._get_switch(route.switch)

        # Acquire locks: instrument → switch (consistent ordering)
        self._acquire_lock_if_needed(connection.instrument, connection_name)
        self._acquire_lock_if_needed(route.switch, connection_name)

        # Close channels
        switch.close_channels(route.channels)

        # Settle
        if route.settling_ms > 0:
            time.sleep(route.settling_ms / 1000.0)

        # Record active state
        self._active_routes[connection_name] = route
        for ch in route.channels:
            self._channel_owners[(route.switch, ch)] = connection_name

        # Emit event
        self._emit_route_closed(connection_name, route)

        logger.debug(
            "Route activated: %s → %s channels %s",
            connection_name,
            route.switch,
            route.channels,
        )

    def deactivate(self, connection_name: str) -> None:
        """Deactivate a switch route for a fixture connection.

        Opens channels and releases channel ownership.
        Locks are retained until ``deactivate_all()`` (session teardown).

        No-op if the route is not active.
        """
        route = self._active_routes.pop(connection_name, None)
        if route is None:
            return

        # Open channels
        switch = self._get_switch(route.switch)
        switch.open_channels(route.channels)

        # Release channel ownership
        for ch in route.channels:
            self._channel_owners.pop((route.switch, ch), None)

        # Emit event
        self._emit_route_opened(connection_name, route)

        logger.debug(
            "Route deactivated: %s → %s channels %s",
            connection_name,
            route.switch,
            route.channels,
        )

    def deactivate_all(self) -> None:
        """Deactivate all active routes and release all locks.

        Called at test teardown (per-test) or session teardown.
        Opens channels in reverse activation order, then releases
        locks in reverse acquisition order (LIFO prevents deadlocks).
        Dict preserves insertion order (Python 3.7+).
        """
        # Deactivate all routes (open channels)
        for connection_name in list(self._active_routes):
            self.deactivate(connection_name)

        # Release all locks in LIFO order (reverse of acquisition)
        for resource_key in reversed(list(self._held_locks)):
            lock = self._held_locks.pop(resource_key)
            lock.release()
            logger.debug("Released lock: %s", resource_key)

    @contextmanager
    def for_pin(self, pin_name: str) -> Generator[None, None, None]:
        """Activate switch route for a DUT pin. Use with direct instrument access.

        Usage::

            with routes.for_pin("VOUT"):
                v = dmm.measure_voltage()

        Args:
            pin_name: DUT pin name (e.g., "VOUT").

        Raises:
            KeyError: If no routed fixture connection for this pin.
        """
        connection_name = self._resolve_pin(pin_name)
        self.activate(connection_name)
        try:
            yield
        finally:
            self.deactivate(connection_name)

    def _resolve_pin(self, pin_name: str) -> str:
        """Resolve a DUT pin name to a fixture connection name."""
        if pin_name not in self._pin_to_connection:
            raise KeyError(
                f"No routed fixture connection for DUT pin '{pin_name}'. "
                f"Available routed pins: {sorted(self._pin_to_connection.keys())}"
            )
        return self._pin_to_connection[pin_name]

    def _check_conflicts(
        self,
        connection_name: str,
        connection: FixtureConnection,
        route: SwitchRoute,
    ) -> None:
        """Check for conflicts with currently active routes."""
        # Channel overlap: another connection using the same switch channels
        for ch in route.channels:
            owner = self._channel_owners.get((route.switch, ch))
            if owner is not None and owner != connection_name:
                raise RouteConflictError(
                    f"Cannot activate route for '{connection_name}': "
                    f"switch channel ({route.switch}, {ch}) is owned by '{owner}'"
                )

        # Instrument channel conflict: same instrument + channel already routed
        inst_key = (connection.instrument, connection.instrument_channel)
        for other_name in self._instrument_channel_map.get(inst_key, []):
            if other_name != connection_name and other_name in self._active_routes:
                raise RouteConflictError(
                    f"Cannot activate route for '{connection_name}': instrument channel "
                    f"({connection.instrument}, {connection.instrument_channel}) "
                    f"is already routed to '{other_name}'"
                )

    def _get_switch(self, switch_role: str) -> SwitchDriver:
        """Resolve a switch role to its driver instance."""
        inst = self._instruments.get(switch_role)
        if inst is None:
            raise KeyError(f"Switch instrument '{switch_role}' not found in active instruments")
        if not self._is_switch(inst):
            missing = [m for m in _SWITCH_METHODS if not callable(getattr(inst, m, None))]
            raise TypeError(
                f"Instrument '{switch_role}' does not implement SwitchDriver protocol. "
                f"Got {type(inst).__name__}. Missing: {', '.join(missing)}"
            )
        return inst  # type: ignore[return-value]

    def _acquire_lock_if_needed(self, role: str, connection_name: str) -> None:
        """Acquire a file lock for an instrument role if not already held.

        Non-switch instruments in ``self._instruments`` are already
        session-locked by InstrumentPool and are skipped here. Switch
        instruments are not in the normal pool, so we lock them ourselves.
        """
        if role in self._held_locks:
            return  # Already locked

        # Non-switch instruments are session-locked by InstrumentPool — skip
        # them here (intentionally silent, not an error). Only lock switch
        # resources that aren't in the normal pool.
        inst = self._instruments.get(role)
        if inst is not None and not self._is_switch(inst):
            return

        meta = ResourceMeta(
            pid=os.getpid(),
            session_id=self._session_id or uuid4(),
            station_id=self._station_id,
            role=role,
            acquired_at=datetime.now(tz=UTC),
        )

        lock = acquire_resource(role, meta, timeout=30)
        self._held_locks[role] = lock
        logger.debug("Acquired route lock: %s", role)

    @staticmethod
    def _is_switch(inst: Any) -> bool:
        """Check if an instrument implements the SwitchDriver protocol."""
        if isinstance(inst, SwitchDriver):
            return True
        return all(callable(getattr(inst, m, None)) for m in _SWITCH_METHODS)

    def _emit_route_closed(self, connection_name: str, route: SwitchRoute) -> None:
        """Emit a RouteClosed event."""
        if self._event_log is None:
            return
        from litmus.data.events import RouteClosed

        kwargs: dict[str, Any] = {
            "connection_name": connection_name,
            "switch_role": route.switch,
            "channels": route.channels,
        }
        if self._session_id is not None:
            kwargs["session_id"] = self._session_id
        self._event_log.emit(RouteClosed(**kwargs))

    def _emit_route_opened(self, connection_name: str, route: SwitchRoute) -> None:
        """Emit a RouteOpened event."""
        if self._event_log is None:
            return
        from litmus.data.events import RouteOpened

        kwargs: dict[str, Any] = {
            "connection_name": connection_name,
            "switch_role": route.switch,
            "channels": route.channels,
        }
        if self._session_id is not None:
            kwargs["session_id"] = self._session_id
        self._event_log.emit(RouteOpened(**kwargs))
