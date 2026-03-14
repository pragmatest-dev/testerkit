"""Route manager for switched signal routing.

Manages the lifecycle of switch routes: lock acquisition (instrument
then switch), channel closure, settling time, conflict detection,
and orderly teardown. Both the ``pins[]`` transparent pattern and
the ``routes.for_pin()`` explicit pattern share this engine.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from filelock._api import BaseFileLock

from litmus.config.test_config import FixturePoint, SwitchRoute
from litmus.instruments.switch import SwitchDriver

logger = logging.getLogger(__name__)


class RouteConflictError(Exception):
    """Raised when a route activation would conflict with an active route."""


class RouteManager:
    """Manages switch route lifecycle, locking, and conflict detection.

    Built at session start from fixture points that have routes.
    Holds locks and active channel state for the duration of the session.

    Lock ordering: instrument resource → switch resource (always).
    This prevents deadlocks when multiple processes share instruments.

    Args:
        points: All fixture points (keyed by point name).
        instruments: Connected instrument instances (keyed by role).
        session_id: Current session UUID (for lock metadata).
        station_id: Station identifier (for lock metadata).
        event_log: Optional event log for emitting route events.
    """

    def __init__(
        self,
        points: dict[str, FixturePoint],
        instruments: dict[str, Any],
        session_id: Any = None,
        station_id: str = "",
        event_log: Any = None,
    ) -> None:
        self._points = points
        self._instruments = instruments
        self._session_id = session_id
        self._station_id = station_id
        self._event_log = event_log

        # Active state
        self._active_routes: dict[str, SwitchRoute] = {}  # point_name → active route
        self._held_locks: dict[str, BaseFileLock] = {}  # resource_key → held lock
        self._channel_owners: dict[tuple[str, str], str] = {}  # (switch_role, channel) → point

        # Build reverse lookup: dut_pin → point_name (for for_pin)
        self._pin_to_point: dict[str, str] = {}
        # Build conflict model: instrument_channel → list of point names
        self._instrument_channel_map: dict[tuple[str, str | None], list[str]] = {}

        for point_name, point in points.items():
            if point.route is not None:
                if point.dut_pin:
                    self._pin_to_point[point.dut_pin] = point_name
                key = (point.instrument, point.instrument_channel)
                self._instrument_channel_map.setdefault(key, []).append(point_name)

    @property
    def has_routes(self) -> bool:
        """True if any fixture points have switch routes."""
        return any(p.route is not None for p in self._points.values())

    @property
    def active_routes(self) -> dict[str, SwitchRoute]:
        """Currently active routes (point_name → SwitchRoute)."""
        return dict(self._active_routes)

    def activate(self, point_name: str) -> None:
        """Activate a switch route for a fixture point.

        Acquires locks (instrument → switch), closes channels, waits
        for settling. No-op if the route is already active.

        Raises:
            KeyError: If point not found or has no route.
            RouteConflictError: If activation would conflict with active routes.
        """
        if point_name in self._active_routes:
            return  # Already active, no-op

        point = self._points.get(point_name)
        if point is None:
            raise KeyError(f"Fixture point '{point_name}' not found")
        route = point.route
        if route is None:
            raise KeyError(f"Fixture point '{point_name}' has no switch route")

        # Check for conflicts
        self._check_conflicts(point_name, point, route)

        # Resolve switch instrument
        switch = self._get_switch(route.switch)

        # Acquire locks: instrument → switch (consistent ordering)
        self._acquire_lock_if_needed(point.instrument, point_name)
        self._acquire_lock_if_needed(route.switch, point_name)

        # Close channels
        switch.close_channels(route.channels)

        # Settle
        if route.settling_ms > 0:
            time.sleep(route.settling_ms / 1000.0)

        # Record active state
        self._active_routes[point_name] = route
        for ch in route.channels:
            self._channel_owners[(route.switch, ch)] = point_name

        # Emit event
        self._emit_route_closed(point_name, route)

        logger.debug(
            "Route activated: %s → %s channels %s",
            point_name, route.switch, route.channels,
        )

    def deactivate(self, point_name: str) -> None:
        """Deactivate a switch route for a fixture point.

        Opens channels and releases channel ownership. Locks are
        retained until ``deactivate_all()`` (session teardown).

        No-op if the route is not active.
        """
        route = self._active_routes.pop(point_name, None)
        if route is None:
            return

        # Open channels
        switch = self._get_switch(route.switch)
        switch.open_channels(route.channels)

        # Release channel ownership
        for ch in route.channels:
            self._channel_owners.pop((route.switch, ch), None)

        # Emit event
        self._emit_route_opened(point_name, route)

        logger.debug(
            "Route deactivated: %s → %s channels %s",
            point_name, route.switch, route.channels,
        )

    def deactivate_all(self) -> None:
        """Deactivate all active routes and release all locks.

        Called at test teardown (per-test) or session teardown.
        Opens channels in reverse activation order, then releases
        locks in reverse acquisition order.
        """
        # Deactivate all routes (open channels)
        for point_name in list(self._active_routes):
            self.deactivate(point_name)

        # Release all locks (reverse order)
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
            KeyError: If no routed fixture point for this pin.
        """
        point_name = self._resolve_pin(pin_name)
        self.activate(point_name)
        try:
            yield
        finally:
            self.deactivate(point_name)

    def _resolve_pin(self, pin_name: str) -> str:
        """Resolve a DUT pin name to a fixture point name."""
        if pin_name not in self._pin_to_point:
            raise KeyError(
                f"No routed fixture point for DUT pin '{pin_name}'. "
                f"Available routed pins: {sorted(self._pin_to_point.keys())}"
            )
        return self._pin_to_point[pin_name]

    def _check_conflicts(
        self, point_name: str, point: FixturePoint, route: SwitchRoute,
    ) -> None:
        """Check for conflicts with currently active routes."""
        # Channel overlap: another point using the same switch channels
        for ch in route.channels:
            owner = self._channel_owners.get((route.switch, ch))
            if owner is not None and owner != point_name:
                raise RouteConflictError(
                    f"Cannot activate route for '{point_name}': "
                    f"switch channel ({route.switch}, {ch}) is owned by '{owner}'"
                )

        # Instrument channel conflict: same instrument + channel already routed
        inst_key = (point.instrument, point.instrument_channel)
        for other_name in self._instrument_channel_map.get(inst_key, []):
            if other_name != point_name and other_name in self._active_routes:
                raise RouteConflictError(
                    f"Cannot activate route for '{point_name}': "
                    f"instrument channel ({point.instrument}, {point.instrument_channel}) "
                    f"is already routed to '{other_name}'"
                )

    def _get_switch(self, switch_role: str) -> SwitchDriver:
        """Resolve a switch role to its driver instance."""
        inst = self._instruments.get(switch_role)
        if inst is None:
            raise KeyError(
                f"Switch instrument '{switch_role}' not found in active instruments"
            )
        # Check protocol structurally: isinstance works for real drivers,
        # but mock instruments use __getattribute__ tricks that bypass
        # runtime_checkable Protocol checks. Fall back to duck-type check.
        if not isinstance(inst, SwitchDriver):
            required = ("close_channels", "open_channels", "open_all")
            missing = [m for m in required if not callable(getattr(inst, m, None))]
            if missing:
                raise TypeError(
                    f"Instrument '{switch_role}' does not implement SwitchDriver protocol. "
                    f"Got {type(inst).__name__}. Missing: {', '.join(missing)}"
                )
        return inst  # type: ignore[return-value]

    def _acquire_lock_if_needed(self, role: str, point_name: str) -> None:
        """Acquire a file lock for an instrument role if not already held.

        In Phase 3a (dedicated instruments per slot), instruments are already
        session-locked by InstrumentPool. We only lock the switch resource
        here since switches aren't in the normal instrument pool. Instrument
        roles that are already session-locked are skipped.
        """
        if role in self._held_locks:
            return  # Already locked

        # Skip locking for non-switch instruments — they're already
        # session-locked by InstrumentPool. Only lock switch resources.
        inst = self._instruments.get(role)
        if inst is not None and not self._is_switch(inst):
            return

        import datetime
        import os
        import uuid

        from litmus.instruments.locks import ResourceMeta, acquire_resource

        meta = ResourceMeta(
            pid=os.getpid(),
            session_id=self._session_id or uuid.uuid4(),
            station_id=self._station_id,
            role=role,
            acquired_at=datetime.datetime.now(tz=datetime.UTC),
        )

        # Use role name as the lock resource (stable, no proxy issues)
        lock = acquire_resource(f"route:{role}", meta, timeout=30)
        self._held_locks[role] = lock
        logger.debug("Acquired route lock: %s", role)

    @staticmethod
    def _is_switch(inst: Any) -> bool:
        """Check if an instrument implements the SwitchDriver protocol."""
        if isinstance(inst, SwitchDriver):
            return True
        required = ("close_channels", "open_channels", "open_all")
        return all(callable(getattr(inst, m, None)) for m in required)

    def _emit_route_closed(self, point_name: str, route: SwitchRoute) -> None:
        """Emit a RouteClosed event."""
        if self._event_log is None:
            return
        from litmus.data.events import RouteClosed

        self._event_log.emit(RouteClosed(
            session_id=self._session_id,
            point_name=point_name,
            switch_role=route.switch,
            channels=route.channels,
        ))

    def _emit_route_opened(self, point_name: str, route: SwitchRoute) -> None:
        """Emit a RouteOpened event."""
        if self._event_log is None:
            return
        from litmus.data.events import RouteOpened

        self._event_log.emit(RouteOpened(
            session_id=self._session_id,
            point_name=point_name,
            switch_role=route.switch,
            channels=route.channels,
        ))
