"""Process-local producer-session primitive.

A *session* is a correlation root on the event spine (OpenTelemetry-trace-shaped):
a client-minted ``session_id`` bracketed by ``SessionStarted`` / ``SessionEnded``.
It holds no scarce resource. Opening one ensures an :class:`EventStore` +
:class:`EventLog` and wires the session-level ``EventStore`` ContextVar; the
returned :class:`SessionScope` brackets the lifecycle.

**Context-local, N-per-process.** A process may hold many concurrent sessions —
e.g. a multiplexing server (``litmus serve``) with one session per client context.
This is NOT a process singleton: :func:`open_session` returns a per-call scope and
the ContextVar isolates them.

**Explicit close is a best-effort, quiescence-proven fast-path** (sole producer, or
an orchestrator post-join). The spine reaper (derived close from the lease) is the
authority. See ``docs/_internal/explorations/session-foundation.md``.

This is the shared core that ``connect()``, the pytest plugin, and the slot runner
build on; the public ``Session`` rename lands in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from litmus.execution._state import get_event_store, set_event_store

if TYPE_CHECKING:
    from litmus.data.event_log import EventLog
    from litmus.data.event_store import EventStore
    from litmus.data.events import SessionStarted


@dataclass
class SessionScope:
    """Handle to an open producer session: its id, stores, and lifecycle brackets.

    Brackets are split into :meth:`emit_ended` (the lifecycle event) and
    :meth:`close_stores` (store teardown) so callers control ordering relative to
    their own capability teardown (e.g. closing a ChannelStore so its subscribers
    see the final flush before the event log shuts down).
    """

    session_id: UUID
    event_store: EventStore
    event_log: EventLog
    owns_event_store: bool
    emit_lifecycle: bool

    def emit_ended(self) -> None:
        """Emit ``SessionEnded`` (best-effort fast-path).

        No-op for attach-only scopes (``emit_lifecycle=False`` — e.g. a multi-slot
        worker attached to the orchestrator's session). The authoritative close is
        the spine reaper; this is the fast-path so a clean end shows immediately.
        """
        from litmus.data.events import SessionEnded

        if self.emit_lifecycle and self.event_log is not None:
            self.event_log.emit(SessionEnded(session_id=self.session_id))

    def close_stores(self) -> None:
        """Close the event log and, if this scope created it, the EventStore.

        Clears the EventStore ContextVar only when this scope owns the store, so an
        attached/reused store isn't torn out from under another scope. Capability
        stores (ChannelStore) are the caller's concern — closed before this so their
        subscribers flush ahead of the event log.
        """
        if self.event_log is not None:
            self.event_log.close()
        if self.owns_event_store and self.event_store is not None:
            self.event_store.close()
            set_event_store(None)


def open_session(
    started: SessionStarted | None,
    *,
    session_id: UUID,
    data_dir: Path | None = None,
    reuse_existing: bool = False,
    emit_lifecycle: bool = True,
) -> SessionScope:
    """Open (or attach to) a producer session.

    Ensures an :class:`EventStore` + :class:`EventLog` for ``session_id``, wires the
    session-level ContextVar, and (when ``emit_lifecycle``) emits ``started``.

    Args:
        started: The pre-built ``SessionStarted`` to emit (carries station identity
            + the will). Caller-built so each entry point supplies its own fields.
            Ignored when ``emit_lifecycle`` is False.
        session_id: The session's correlation id (client-minted, or injected for a
            multi-slot worker attaching to the orchestrator's session).
        data_dir: Where a freshly-created EventStore writes. Ignored when reusing.
        reuse_existing: Reuse an EventStore already in the ContextVar if present
            (the pytest setup path); otherwise always create a fresh one (connect).
            Ownership — and thus who closes it — follows from whether we created it.
        emit_lifecycle: Emit ``SessionStarted``/``SessionEnded``. False for a
            multi-slot worker that attaches to the orchestrator's injected session.

    Returns:
        A :class:`SessionScope` handle. Caller brackets the end with
        :meth:`SessionScope.emit_ended` + :meth:`SessionScope.close_stores`.
    """
    from litmus.data.event_store import EventStore

    event_store = get_event_store() if reuse_existing else None
    owns_event_store = event_store is None
    if owns_event_store:
        event_store = EventStore(_data_dir=data_dir)
        set_event_store(event_store)
    event_log = event_store.get_event_log(session_id)
    if emit_lifecycle and started is not None:
        event_log.emit(started)
    return SessionScope(
        session_id=session_id,
        event_store=event_store,
        event_log=event_log,
        owns_event_store=owns_event_store,
        emit_lifecycle=emit_lifecycle,
    )
