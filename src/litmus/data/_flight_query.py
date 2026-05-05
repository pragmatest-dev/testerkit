"""Shared Flight query helper with retry logic.

Used by EventStore and RunStore to avoid duplicating the same
connect → query → retry → re-acquire pattern.

Channel pooling
---------------

``flight.FlightClient`` instances are pooled per ``location``
process-wide. ``litmus serve`` constructs new ``RunsQuery`` /
``StepsQuery`` / ``EventStore`` per page render — without
pooling, each construction → a fresh gRPC channel → a fresh
client thread pool. ``FlightClient.close()`` doesn't fully
release gRPC C++ thread resources synchronously, so threads
accumulated monotonically with UI activity. The ``litmus serve``
process eventually hit an internal gRPC limit and aborted with
``std::system_error: Resource temporarily unavailable``.

Pooling keeps ONE client per daemon location for the lifetime
of the process. ``FlightQueryClient.close()`` becomes a no-op
on the underlying client (still releases the manager ref count
the caller holds). Process exit drops everything via the
gRPC C++ runtime's shutdown path.
"""

from __future__ import annotations

import threading
import time
import warnings
from collections.abc import Callable
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight


class IndexOutOfDate(Exception):
    """Raised when the DuckDB index schema doesn't match the current code."""


# Process-wide pool: one ``FlightClient`` per ``location``.
# Reused across every ``FlightQueryClient`` that targets the same
# daemon. Lock guards both the dict and the "client is alive"
# invariant against concurrent reset/close attempts.
_CLIENT_POOL: dict[str, flight.FlightClient] = {}
_CLIENT_POOL_LOCK = threading.Lock()


def _get_pooled_client(location: str) -> flight.FlightClient:
    """Return the process-wide ``FlightClient`` for ``location``, creating one if needed."""
    with _CLIENT_POOL_LOCK:
        client = _CLIENT_POOL.get(location)
        if client is None:
            client = flight.connect(location)
            _CLIENT_POOL[location] = client
        return client


def _drop_pooled_client(location: str) -> None:
    """Remove a pooled client (e.g. after a transient gRPC failure).

    Lets the next ``_get_pooled_client(location)`` reconnect. The
    dropped client is closed best-effort — gRPC C++ shutdown is
    eventual, but we don't block the caller.
    """
    with _CLIENT_POOL_LOCK:
        client = _CLIENT_POOL.pop(location, None)
    if client is not None:
        try:
            client.close()
        except (flight.FlightError, OSError, pa.ArrowException):
            pass


class FlightQueryClient:
    """Lazy Flight client with retrying SQL queries.

    Parameters:
        location: gRPC location string (e.g. "grpc://127.0.0.1:12345").
        ticket_prefix: Prefix for SQL tickets (e.g. "events", "runs").
        reacquire: Called on transient failure to get a fresh location.
        label: Human label for warning messages (e.g. "EventStore").
    """

    __slots__ = ("_label", "_location", "_reacquire", "_ticket_prefix")

    def __init__(
        self,
        location: str,
        ticket_prefix: str,
        *,
        reacquire: Callable[[], str] | None = None,
        label: str = "FlightQueryClient",
    ) -> None:
        self._location = location
        self._ticket_prefix = ticket_prefix
        self._reacquire = reacquire
        self._label = label

    @property
    def location(self) -> str:
        return self._location

    def get_client(self) -> flight.FlightClient:
        """Get the process-wide Flight client for this location."""
        return _get_pooled_client(self._location)

    def query(self, sql: str, *, _retries: int = 2) -> list[dict[str, Any]]:
        """Execute a SQL query via Flight and return list of dicts.

        Retries on transient gRPC errors (e.g. daemon restart).
        DuckDB raises errors back through the Flight stream as
        ``flight.FlightError`` with the original error text inline —
        we string-match for the two expected DuckDB error messages
        (``measurements`` view missing during cold start, "Binder
        Error" on index schema drift) because DuckDB's typed exceptions
        don't survive the gRPC round-trip.
        """
        last_exc: flight.FlightError | OSError | pa.ArrowException | None = None
        for attempt in range(_retries + 1):
            try:
                client = self.get_client()
                ticket = flight.Ticket(
                    f"{self._ticket_prefix}\0{sql}".encode(),
                )
                reader = client.do_get(ticket)
                table = reader.read_all()
                return table.to_pylist()
            except (flight.FlightError, OSError, pa.ArrowException) as exc:
                err_msg = str(exc)
                # Cold start: ``measurements`` view not yet created in
                # the runs daemon (no measurement parquets on disk).
                # Treat any reference to a missing ``measurements``
                # relation — Catalog Error / Binder Error / "Table not
                # found" — as an empty result rather than 500.
                if "measurements" in err_msg and (
                    "does not exist" in err_msg
                    or "not found" in err_msg
                    or "Table with name measurements" in err_msg
                ):
                    return []
                last_exc = exc
                # Drop the pooled client so the next attempt
                # reconnects — daemon may have restarted.
                _drop_pooled_client(self._location)
                if attempt < _retries:
                    time.sleep(0.2)
                    if self._reacquire is not None:
                        try:
                            self._location = self._reacquire()
                        except (ValueError, OSError):
                            pass
        if last_exc and "Binder Error" in str(last_exc):
            raise IndexOutOfDate(
                "Index is out of date. Run `litmus data reindex` to rebuild."
            ) from last_exc
        warnings.warn(
            f"{self._label} Flight query failed after {_retries + 1} attempts: {last_exc}",
            stacklevel=3,
        )
        return []

    def reset(self) -> None:
        """Drop the pooled client for this location so the next query reconnects."""
        _drop_pooled_client(self._location)

    def close(self) -> None:
        """No-op on the pooled client.

        The pooled ``FlightClient`` is shared across every
        ``FlightQueryClient`` instance targeting the same
        location. Closing here would invalidate everyone else's
        next query and reintroduce the per-call thread churn that
        made ``litmus serve`` abort. The pool is released only at
        process exit (via gRPC's shutdown path) or on a transient
        gRPC error (via :func:`_drop_pooled_client`).
        """
        return
