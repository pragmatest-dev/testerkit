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

from litmus.data._flight_retry import with_retry

# Default deadline for one-shot request/response Flight calls (queries +
# one-shot puts). Local calls are milliseconds, so this only ever fires on a
# wedged or dead daemon — converting an otherwise-infinite hang into a
# FlightTimedOutError that ``with_retry`` classifies TRANSIENT and recovers
# from. NOT applied to long-lived streams (subscriptions, the channels held
# do_put writer), where a deadline would wrongly tear the stream down.
# Client-side by necessity: a wedged server can't enforce its own timeout.
# Phase F (remote backend) will source this from ProjectConfig per deployment.
DEFAULT_DAEMON_TIMEOUT_S = 30.0


def call_options(timeout_s: float = DEFAULT_DAEMON_TIMEOUT_S) -> flight.FlightCallOptions:
    """FlightCallOptions carrying a client deadline for one-shot calls."""
    return flight.FlightCallOptions(timeout=timeout_s)


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


def probe_sql(location: str, db_name: str, timeout_s: float = 5.0) -> bool:
    """Liveness probe for a DuckDBFlightServer daemon (events / runs / files).

    Runs a literal ``SELECT 1`` (no FROM) so it tests CONNECTIVITY, not data —
    it returns a row even on a cold daemon with no tables yet, and an empty or
    any result counts as alive. Only an exception (dead Flight thread, deadline
    on a wedged daemon) marks it down; the broken client is dropped so the next
    caller reconnects. Short deadline so a wedged daemon fails fast.
    """
    try:
        client = _get_pooled_client(location)
        client.do_get(
            flight.Ticket(f"{db_name}\0SELECT 1".encode()), options=call_options(timeout_s)
        ).read_all()
        return True
    except Exception:  # noqa: BLE001 — any failure means "respawn it"
        _drop_pooled_client(location)
        return False


def probe_flights(location: str, timeout_s: float = 5.0) -> bool:
    """Liveness probe for a ChannelFlightServer daemon (no SQL surface).

    Uses ``list_flights`` — a server-level call that responds even when the
    daemon holds zero channels. An EMPTY list is alive (the server answered);
    only an exception marks it down. (A "non-empty only" check would falsely
    kill a brand-new daemon on every acquire.)
    """
    try:
        client = _get_pooled_client(location)
        list(client.list_flights(options=call_options(timeout_s)))
        return True
    except Exception:  # noqa: BLE001 — any failure means "respawn it"
        _drop_pooled_client(location)
        return False


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
            reader = client.do_get(ticket, options=call_options())
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
