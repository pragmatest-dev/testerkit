"""Transparent instrument proxy that emits events on driver calls.

Wraps any driver object and intercepts method calls to push
InstrumentRead, InstrumentSet, or InstrumentConfigure events
to the event log. Test code is unaffected — the proxy is applied
at fixture creation time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from litmus.data.event_log import EventLog
from litmus.data.events import InstrumentConfigure, InstrumentRead, InstrumentSet

_READ_PREFIXES = ("measure_", "read_", "get_", "query_", "fetch_")
_SET_PREFIXES = ("set_", "write_")
_CONFIGURE_PREFIXES = ("configure_", "setup_", "init_")
_PASSTHROUGH = frozenset({
    "connect", "disconnect", "close",
    "__enter__", "__exit__",
    "set_mock_value",
})


def _classify(name: str) -> str:
    """Classify a method name as read, set, or configure."""
    for prefix in _READ_PREFIXES:
        if name.startswith(prefix):
            return "read"
    for prefix in _SET_PREFIXES:
        if name.startswith(prefix):
            return "set"
    for prefix in _CONFIGURE_PREFIXES:
        if name.startswith(prefix):
            return "configure"
    return "configure"


def _strip_prefix(name: str, classification: str) -> str:
    """Strip the classification prefix to derive a channel stem."""
    prefixes = {
        "read": _READ_PREFIXES,
        "set": _SET_PREFIXES,
        "configure": _CONFIGURE_PREFIXES,
    }
    for prefix in prefixes.get(classification, ()):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


class InstrumentProxy:
    """Transparent proxy that emits events for instrument driver calls.

    Wraps a driver instance and intercepts attribute access. Callable
    attributes (methods) are wrapped to emit events before/after the
    real call. Non-callable attributes pass through directly.

    Method classification (by name prefix) drives the event type:

    - **Read** (``measure_``, ``read_``, ``get_``, ``query_``, ``fetch_``)
      → emits ``InstrumentRead``, channel_id = ``{role}.{stem}``
    - **Set** (``set_``, ``write_``)
      → emits ``InstrumentSet``, channel_id = ``{role}.{stem}``
    - **Configure** (``configure_``, ``setup_``, ``init_``, or unrecognized)
      → emits ``InstrumentConfigure`` (no channel_id, no numeric data)

    Lifecycle methods (connect, disconnect, close) pass through without
    events — the plugin handles those via InstrumentConnected/Disconnected.
    """

    def __init__(
        self,
        driver: Any,
        role: str,
        event_log: EventLog,
        session_id: UUID,
        run_id: UUID | None = None,
    ) -> None:
        object.__setattr__(self, "_driver", driver)
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_event_log", event_log)
        object.__setattr__(self, "_session_id", session_id)
        object.__setattr__(self, "_run_id", run_id)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._driver, name)

        if name.startswith("_") or name in _PASSTHROUGH:
            return attr

        if callable(attr):
            return self._wrap_call(name, attr)

        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._driver, name, value)

    def _wrap_call(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Return a wrapper that calls fn and emits the appropriate event."""
        classification = _classify(name)
        role = self._role
        session_id = self._session_id
        run_id = self._run_id
        event_log = self._event_log

        if classification == "read":
            channel_id = f"{role}.{_strip_prefix(name, classification)}"

            def read_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = fn(*args, **kwargs)
                event_log.emit(InstrumentRead(
                    session_id=session_id,
                    run_id=run_id,
                    instrument_role=role,
                    channel_id=channel_id,
                    method=name,
                    value=result,
                ))
                return result

            return read_wrapper

        if classification == "set":
            channel_id = f"{role}.{_strip_prefix(name, classification)}"

            def set_wrapper(*args: Any, **kwargs: Any) -> Any:
                value = args[0] if args else kwargs.get("value")
                event_log.emit(InstrumentSet(
                    session_id=session_id,
                    run_id=run_id,
                    instrument_role=role,
                    channel_id=channel_id,
                    attribute=_strip_prefix(name, classification),
                    value=value,
                ))
                return fn(*args, **kwargs)

            return set_wrapper

        # configure
        def configure_wrapper(*args: Any, **kwargs: Any) -> Any:
            event_log.emit(InstrumentConfigure(
                session_id=session_id,
                run_id=run_id,
                instrument_role=role,
                method=name,
                parameters=kwargs,
            ))
            return fn(*args, **kwargs)

        return configure_wrapper

    def __repr__(self) -> str:
        return f"InstrumentProxy({self._role!r}, {self._driver!r})"
