"""Classify Flight / DuckDB-over-gRPC errors so callers retry only what's worth retrying.

Background
----------

PyArrow Flight serializes server-side exceptions to plain
``flight.FlightError`` strings — typed DuckDB exceptions (Binder
Error, Catalog Error, etc.) don't survive the gRPC round-trip.
That forces callers into string-matching, which we'd rather do in
ONE place than every retry loop.

The mature pattern, from gRPC's own retry guidance and Microsoft's
Azure SQL transient-error docs:

* **Connection-level errors** (daemon mid-restart, channel torn,
  deadline exceeded) — retry. The same query against a fresh
  channel will often succeed.
* **Query-level errors** (Binder, Catalog, syntax, type
  mismatch) — fail fast. Retrying the same query against the
  same daemon re-produces the same error and burns time.

Conflating the two is what made one bad column ("Binder Error:
``uut_lot_number`` does not exist") hang the /results page handler
for ~7s and surface as ``ERR_EMPTY_RESPONSE``.

References
----------

* gRPC retry policy (gRFC A6) — only retry ``UNAVAILABLE`` /
  ``RESOURCE_EXHAUSTED``; never ``INVALID_ARGUMENT`` or
  ``FAILED_PRECONDITION``.
  https://github.com/grpc/proposal/blob/master/A6-client-retries.md
* Azure SQL transient errors — "SQL Error 102, 'Incorrect syntax,'
  won't go away no matter how many times you submit the same query."
  https://learn.microsoft.com/en-us/azure/azure-sql/database/troubleshoot-common-connectivity-issues
"""

from __future__ import annotations

from enum import Enum

import pyarrow as pa
import pyarrow.flight as flight


class FlightQueryError(Exception):
    """Base for any Flight query failure with a classified kind."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class FlightPermanentError(FlightQueryError):
    """SQL / schema / arg error. Retry won't help.

    Common shapes (matched on the FlightError message):

    * ``Binder Error: column "X" does not exist``
    * ``Catalog Error: table "Y" does not exist``
    * ``Parser Error: syntax error at or near "..."``
    * ``Conversion Error: ...``

    Subclassing is intentionally light — callers that need
    fine-grained dispatch can ``isinstance`` against the message;
    most callers just want to surface the original DuckDB text in
    a 500 response or a UI fallback.
    """


class FlightTransientError(FlightQueryError):
    """Connection / availability / timeout. Retry may help.

    Common shapes:

    * ``OSError`` (any) — gRPC channel torn, daemon respawning.
    * ``flight.FlightError`` containing "unavailable",
      "connection refused", "connection reset", "deadline
      exceeded", "transport closed".
    """


class FlightErrorKind(Enum):
    """How a Flight call failed, with the right recovery strategy."""

    TRANSIENT = "transient"
    """Retry after a brief wait + drop the pooled client."""

    PERMANENT = "permanent"
    """Raise immediately. Retrying is a waste of cycles."""

    EMPTY_OK = "empty_ok"
    """Cold-start "table doesn't exist yet" — caller should treat
    as an empty result, not an error. Right now this is just the
    ``measurements`` view, which the runs daemon defers until the
    first measurement parquet lands."""


# Substrings (case-insensitive) that mark a FlightError as
# transient (connection-level). Sourced from gRPC core status
# code documentation + observed pyarrow Flight wrappings.
_TRANSIENT_MARKERS = (
    "unavailable",
    "connection refused",
    "connection reset",
    "deadline exceeded",
    "transport closed",
    "rpc cancelled",
    "broken pipe",
)

# Substrings (case-insensitive) that mark a FlightError as a
# DuckDB query-side error — won't recover from retry.
_PERMANENT_MARKERS = (
    "binder error",
    "catalog error",
    "parser error",
    "conversion error",
    "constraint error",
    "type mismatch",
    "invalid input",
    "out of range",
)

# Marker for the cold-start measurements-view-not-yet-created
# case. Today this only fires for the runs daemon's deferred view.
_EMPTY_OK_MARKERS = ("measurements",)


def classify(exc: Exception) -> FlightErrorKind:
    """Return the right recovery strategy for ``exc``.

    Default-deny: anything we can't positively identify as
    transient or empty-ok lands in PERMANENT. That's the safe
    direction — known-unknowns fail loudly instead of silently
    hammering the daemon with retries.

    The classification is by **substring on the message** because
    pyarrow Flight serializes typed exceptions to plain strings.
    See the module docstring for context.
    """
    if isinstance(exc, OSError):
        return FlightErrorKind.TRANSIENT

    # Typed connection-level Flight errors — a client-set deadline firing on a
    # wedged/dead daemon, an unavailable channel, or a cancelled call. Check by
    # TYPE before message-substring matching: it's more reliable than parsing
    # the serialized string, and it's what makes the client timeout recoverable
    # (the deadline raises FlightTimedOutError → TRANSIENT → with_retry).
    if isinstance(
        exc,
        flight.FlightTimedOutError | flight.FlightUnavailableError | flight.FlightCancelledError,
    ):
        return FlightErrorKind.TRANSIENT

    if isinstance(exc, flight.FlightError):
        msg = str(exc).lower()

        # Cold-start "view not yet created" — caller wants empty,
        # not a 500. Narrow check: must mention the relation by
        # name AND have a "doesn't exist" / "not found" verb.
        if any(m in msg for m in _EMPTY_OK_MARKERS) and (
            "does not exist" in msg or "not found" in msg or "table with name" in msg
        ):
            return FlightErrorKind.EMPTY_OK

        # Query-side errors first — these are the high-value
        # fail-fast cases. A Binder Error in the message is more
        # specific than a generic "unavailable" so check it first.
        if any(m in msg for m in _PERMANENT_MARKERS):
            return FlightErrorKind.PERMANENT

        if any(m in msg for m in _TRANSIENT_MARKERS):
            return FlightErrorKind.TRANSIENT

        # Default: treat unknown FlightErrors as PERMANENT so we
        # don't loop forever on something we don't understand.
        return FlightErrorKind.PERMANENT

    if isinstance(exc, pa.ArrowException):
        # Arrow-level errors are usually data shape / schema / IPC
        # frame issues. Retrying re-reads the same bad bytes.
        return FlightErrorKind.PERMANENT

    return FlightErrorKind.PERMANENT


def wrap(exc: Exception) -> FlightQueryError:
    """Promote a classified ``Exception`` into a typed ``FlightQueryError``.

    Used by the retry helper to surface the right exception to
    callers — ``FlightPermanentError`` for SQL bugs, ``FlightTransientError``
    once retries are exhausted. The original exception is kept on
    ``.cause`` for full debug context.
    """
    kind = classify(exc)
    if kind is FlightErrorKind.TRANSIENT:
        return FlightTransientError(str(exc), cause=exc)
    return FlightPermanentError(str(exc), cause=exc)
