"""Observer contract and InstrumentEventEmitter for instrument proxies.

The proxy delegates all interpretation to a ``DriverObserver`` subclass.
Each driver library gets its own observer that understands the library's
API conventions (descriptors, prefixes, SCPI, etc.).

``InstrumentEventEmitter`` encapsulates event construction, channel store writes,
and session plumbing so observers just call ``emit.read("voltage", 3.3)``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from litmus.data.event_log import EventLog
from litmus.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from litmus.data.files import get_filestore
from litmus.data.ref import classify_value
from litmus.execution._state import get_current_context

logger = logging.getLogger(__name__)

LIFECYCLE_METHODS = frozenset(
    {
        "connect",
        "disconnect",
        "close",
        "shutdown",
        "reset",
        "__enter__",
        "__exit__",
        "__init__",
        "__del__",
        "__repr__",
        "__str__",
        "__hash__",
        "__eq__",
        "set_mock_value",
    }
)
"""Methods the observer should never emit events for."""


class InstrumentEventEmitter:
    """Passed to observers. Handles event log, channel store, session IDs."""

    def __init__(
        self,
        event_log: EventLog,
        session_id: UUID,
        role: str,
        run_id: UUID | None = None,
        resource: str = "",
        channel_store: Any | None = None,
    ) -> None:
        self._event_log = event_log
        self._session_id = session_id
        self._role = role
        self._run_id = run_id
        self._resource = resource
        self._channel_store = channel_store
        # Per-(channel, session) "started" tracking lives on
        # :class:`ChannelStore` when one is wired (item 4b
        # consolidation) — the store emits ``ChannelStarted`` exactly
        # once per (channel, session) regardless of writer path.
        # When no store is wired (bringup tier: driver used outside a
        # station / session), this set is the fallback tracker so the
        # event still fires for observability of the bare interaction.
        self._fallback_started_channels: set[str] = set()

    def _store_value(self, channel_id: str, value: Any, source: str) -> Any:
        """Route a value to the right store; return URI or the raw value.

        - Blobs (PIL image, raw bytes, Path, Pydantic model, anything
          ``classify_value`` flags as ``"blob"``) route through
          :func:`FileStore.write` (build item 3b). The returned
          ``file://`` URI is written into ChannelStore as the channel's
          sample value — a ``scalar:str`` channel (relies on C2's typed
          leaf-types, build item 14) so the live channel timeline
          carries the claim-check. URI returned to the caller for
          ``out_*`` stamping.
        - Channel-shaped numerics (scalar / list / numpy array / dict)
          go straight to ChannelStore as before; its ``write`` returns
          a ``channel://`` URI for downstream stamping.
        - When no ChannelStore is wired (driver outside a session), the
          raw value is returned unchanged — blobs are still dropped in
          that path because there's no session to scope a FileStore put
          to. This matches pre-3b behavior for the no-session case.

        ``ChannelStarted`` lifecycle event (Position 2) is emitted by
        :class:`ChannelStore.write` on its first per-(channel, session)
        write — not here. ``instrument_role`` / ``resource`` / ``run_id``
        are passed through so ChannelStore can populate the event.
        """
        if self._channel_store is None or value is None:
            return value

        vtype = classify_value(value)
        if vtype == "blob":
            uri = get_filestore().write(channel_id, value, session_id=str(self._session_id))
            try:
                self._channel_store.write(
                    channel_id,
                    uri,
                    source=source,
                    instrument_role=self._role,
                    resource=self._resource,
                    run_id=self._run_id,
                )
            except (OSError, ValueError, TypeError) as exc:
                logger.debug("Channel store URI write failed for %s: %s", channel_id, exc)
            return uri

        try:
            return self._channel_store.write(
                channel_id,
                value,
                source=source,
                instrument_role=self._role,
                resource=self._resource,
                run_id=self._run_id,
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Channel store write failed for %s: %s", channel_id, exc)
        return value

    def read(self, channel: str, value: Any, method: str = "") -> None:
        """Record an instrument read.

        Under Position 2 (lifecycle-only channel events): the per-sample
        ``InstrumentRead`` event is retired. This method:

        1. Writes the value to ChannelStore (sample data lives there).
           ``ChannelStore.write`` emits ``ChannelStarted`` on the first
           per-(channel, session) write; subsequent writes are silent
           on the event timeline. Subscribers wanting per-sample access
           subscribe to ChannelStore via Flight ``do_get``.
        2. Stamps the active harness Context's ``out_<channel>`` with
           the channel URI on first write per (vector, channel). This
           lets ``verify`` measurement rows reference the channel via
           a denormalized column. ``setdefault`` makes it idempotent.

        Bringup-tier fallback: when no ChannelStore is wired (driver
        used outside a station / session), this method emits
        ``ChannelStarted`` directly so the observability surface stays
        coherent for ad-hoc / interactive driver use.
        """
        stored_value = self._store_value(channel, value, method)

        # Bringup-tier fallback: no ChannelStore to own emission.
        if self._channel_store is None and channel not in self._fallback_started_channels:
            self._fallback_started_channels.add(channel)
            self._event_log.emit(
                ChannelStarted(
                    session_id=self._session_id,
                    run_id=self._run_id,
                    channel_id=channel,
                    instrument_role=self._role,
                    method=method,
                    resource=self._resource,
                )
            )

        # Stamp active vector's out_<channel> on first write per
        # (vector, channel). Idempotent via setdefault.
        ctx = get_current_context()
        if ctx is not None and isinstance(stored_value, str):
            # Only stamp when we have a URI (channel store wrote and
            # returned channel://...). For inline scalars, the row's
            # measurement.value carries the data; out_* not needed.
            ctx._observations.setdefault(channel, stored_value)

    def set(self, channel: str, value: Any, attr: str = "") -> None:
        """Emit an InstrumentSet event."""
        event_value = self._store_value(channel, value, attr)
        self._event_log.emit(
            InstrumentSet(
                session_id=self._session_id,
                run_id=self._run_id,
                instrument_role=self._role,
                channel_id=channel,
                attribute=attr,
                value=event_value,
                resource=self._resource,
            )
        )

    def configure(self, method: str, parameters: dict[str, Any]) -> None:
        """Emit an InstrumentConfigure event."""
        self._event_log.emit(
            InstrumentConfigure(
                session_id=self._session_id,
                run_id=self._run_id,
                instrument_role=self._role,
                method=method,
                parameters=parameters,
                resource=self._resource,
            )
        )


class DriverObserver:
    """Observes driver interactions and emits events.

    The proxy calls these hooks on every interaction. The observer
    interprets what happened and emits appropriate events. Each driver
    library gets its own observer subclass.

    Subclasses set ``observer_protocols`` to auto-register::

        class MyObserver(DriverObserver):
            observer_protocols = ["mylib"]
    """

    observer_protocols: list[str] = []
    """Protocol names this observer handles. Set to auto-register."""

    _silent_methods: frozenset[str] = frozenset()
    """Per-subclass methods to skip (in addition to LIFECYCLE_METHODS)."""

    _registry: dict[str, type[DriverObserver]] = {}
    """Maps protocol name → observer class. Populated by __init_subclass__."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name in cls.observer_protocols:
            DriverObserver._registry[name] = cls

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: InstrumentEventEmitter,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        """Inspect driver_class once, build lookup tables."""
        self.role = role
        self.emit = emit

    def _should_skip(self, name: str) -> bool:
        """True for private, lifecycle, or observer-specific silent methods."""
        return name.startswith("_") or name in LIFECYCLE_METHODS or name in self._silent_methods

    def on_getattr(self, name: str, value: Any) -> Any:
        """Called on every non-callable attribute access.

        Default: return value unchanged (passthrough).
        """
        return value

    def on_setattr(self, name: str, value: Any) -> None:
        """Called on every attribute set.

        Default: no-op.
        """

    def on_call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        """Called after every method call with the result.

        Default: no-op.
        """
