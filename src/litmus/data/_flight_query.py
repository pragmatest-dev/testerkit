"""Shared Flight query helper with classified retry logic.

Used by EventStore, RunStore, RunsQuery, StepsQuery,
MeasurementsQuery — every read path that talks to a litmus daemon
over Flight.

Two responsibilities, both shared across the site:

1. **Channel pooling**: one ``flight.FlightClient`` per location
   per process. ``litmus serve`` constructs fresh
   ``RunsQuery`` / ``StepsQuery`` / ``EventStore`` per page
   render — without pooling, each construction opened a new gRPC
   channel + client thread pool. ``FlightClient.close()`` doesn't
   release gRPC C++ thread resources synchronously, so threads
   accumulated monotonically with UI activity until the serve
   process aborted with ``std::system_error: Resource temporarily
   unavailable``. Pooling keeps ONE client per daemon location
   for the lifetime of the process; ``FlightQueryClient.close()``
   is a no-op on the underlying client.

2. **Selective retry**: errors are classified via
   :mod:`litmus.data._flight_errors` and routed through
   :func:`litmus.data._flight_retry.with_retry`. Transient
   failures (daemon mid-restart, gRPC deadline) retry with
   exponential backoff. Permanent failures (Binder Error,
   Catalog Error, syntax) raise immediately. The legacy
   "retry everything" loop turned a single bad column into a
   ~7s page hang; classified retry returns in milliseconds with
   a typed exception the caller can render.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data._flight_errors import (
    FlightQueryError,  # noqa: F401 — re-exported for callers
    FlightTransientError,  # noqa: F401 — re-exported for callers
    IndexOutOfDate,  # noqa: F401 — back-compat alias re-exported
)
from litmus.data._flight_retry import with_retry

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

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query via Flight and return list of dicts.

        Selective retry: TRANSIENT errors (daemon mid-restart,
        gRPC channel torn) retry up to 3× with exponential
        backoff + jitter. PERMANENT errors (Binder Error, Catalog
        Error, syntax) raise immediately as
        :class:`FlightPermanentError`. EMPTY_OK errors (cold-start
        ``measurements`` view) return ``[]``. See
        :mod:`litmus.data._flight_errors` for the classification.
        """

        def _do_query() -> list[dict[str, Any]]:
            client = _get_pooled_client(self._location)
            ticket = flight.Ticket(f"{self._ticket_prefix}\0{sql}".encode())
            reader = client.do_get(ticket)
            return reader.read_all().to_pylist()

        def _drop() -> None:
            _drop_pooled_client(self._location)

        def _reacquire() -> None:
            if self._reacquire is not None:
                try:
                    self._location = self._reacquire()
                except (ValueError, OSError):
                    pass

        return with_retry(
            _do_query,
            on_drop=_drop,
            on_reacquire=_reacquire,
            on_empty=list,
            label=self._label,
        )

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
