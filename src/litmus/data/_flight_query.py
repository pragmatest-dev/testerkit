"""Shared Flight query helper with retry logic.

Used by EventStore and RunStore to avoid duplicating the same
connect → query → retry → re-acquire pattern.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight


class IndexOutOfDate(Exception):
    """Raised when the DuckDB index schema doesn't match the current code."""


class FlightQueryClient:
    """Lazy Flight client with retrying SQL queries.

    Parameters:
        location: gRPC location string (e.g. "grpc://127.0.0.1:12345").
        ticket_prefix: Prefix for SQL tickets (e.g. "events", "runs").
        reacquire: Called on transient failure to get a fresh location.
        label: Human label for warning messages (e.g. "EventStore").
    """

    __slots__ = ("_client", "_label", "_location", "_reacquire", "_ticket_prefix")

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
        self._client: flight.FlightClient | None = None

    @property
    def location(self) -> str:
        return self._location

    def get_client(self) -> flight.FlightClient:
        """Get or create a Flight client."""
        if self._client is None:
            self._client = flight.connect(self._location)
        return self._client

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
                # Cold start: measurements view not yet created in the
                # runs daemon. Treat as empty result set rather than retry.
                if "measurements" in err_msg and "does not exist" in err_msg:
                    return []
                last_exc = exc
                self._client = None
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
        """Drop the cached client (e.g. after a non-fatal error)."""
        self._client = None

    def close(self) -> None:
        """Close the Flight client."""
        if self._client is not None:
            try:
                self._client.close()
            except (flight.FlightError, OSError, pa.ArrowException) as exc:
                warnings.warn(
                    f"Failed to close Flight client: {exc}",
                    stacklevel=2,
                )
            self._client = None
