"""Selective retry helper — retry TRANSIENT failures, fail-fast on PERMANENT.

Used by every Flight callsite (read AND write) so retry behaviour is
consistent: a daemon mid-restart recovers automatically, a SQL Binder
Error fails immediately. See ``_flight_errors`` for the classification.

Reference shape: tenacity's ``retry_if_exception_type`` — declare
which kinds warrant retry, let everything else escape.
https://tenacity.readthedocs.io/
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from testerkit.data._flight_errors import (
    FlightErrorKind,
    FlightTransientError,
    classify,
    wrap,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    on_drop: Callable[[], None] | None = None,
    on_reacquire: Callable[[], None] | None = None,
    on_empty: Callable[[], T] | None = None,
    max_attempts: int = 3,
    base_delay_s: float = 0.2,
    label: str = "Flight",
) -> T:
    """Run ``fn`` with classified retry semantics.

    On :class:`FlightErrorKind.TRANSIENT` — drop the pooled client
    (``on_drop``), optionally re-acquire (``on_reacquire``), sleep
    with exponential backoff + jitter, retry up to ``max_attempts``
    times. The gRPC retry guidance recommends jitter to avoid
    thundering-herd on a recovering daemon
    (https://github.com/grpc/proposal/blob/master/A6-client-retries.md).

    On :class:`FlightErrorKind.PERMANENT` — raise
    :class:`FlightPermanentError` immediately. **No retry**, no
    sleep. The original DuckDB message is preserved on the
    exception's args + ``.cause``.

    On :class:`FlightErrorKind.EMPTY_OK` — call ``on_empty()``
    (the caller's "treat as empty" sentinel) and return its
    result. Used by query callers that want a missing
    ``measurements`` view to surface as ``[]`` instead of an error.

    Once retries are exhausted, raises :class:`FlightTransientError`
    with the last seen exception on ``.cause``.

    Parameters
    ----------
    fn:
        The operation to execute. Called fresh on each attempt.
    on_drop:
        Called BEFORE each retry. Used to drop the pooled
        ``FlightClient`` for the location so the next attempt
        reconnects.
    on_reacquire:
        Called BEFORE each retry, AFTER ``on_drop``. Used to
        re-resolve the daemon's gRPC location if it may have
        respawned on a fresh port.
    on_empty:
        Returned (after calling) when the underlying error
        classifies as ``EMPTY_OK``. Default raises
        ``FlightPermanentError`` since most callers don't have an
        empty-result fallback.
    max_attempts:
        Total attempts including the first try. Default 3.
    base_delay_s:
        Initial sleep before the second attempt. Doubles each
        subsequent attempt, with up to 25 % jitter.
    label:
        Prefix for log messages so an operator can grep ``[runs
        flight] ...`` vs ``[events flight] ...``.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — we re-raise after classify
            kind = classify(exc)
            last_exc = exc

            if kind is FlightErrorKind.PERMANENT:
                # Fail fast. The query is wrong, the schema doesn't
                # match, or the data is corrupt — retrying is a
                # waste of cycles and just makes the page hang.
                raise wrap(exc) from exc

            if kind is FlightErrorKind.EMPTY_OK:
                if on_empty is None:
                    # Caller didn't opt in to empty-result fallback;
                    # surface as a normal permanent error so the
                    # caller can decide.
                    raise wrap(exc) from exc
                return on_empty()

            # TRANSIENT — drop, re-acquire, backoff, retry.
            if on_drop is not None:
                try:
                    on_drop()
                except Exception:  # noqa: BLE001 — drop is best-effort
                    pass
            if on_reacquire is not None:
                try:
                    on_reacquire()
                except Exception:  # noqa: BLE001 — reacquire is best-effort
                    pass

            if attempt + 1 >= max_attempts:
                break

            # Exponential backoff with up to 25 % jitter so a
            # crowd of stalled callers doesn't slam the daemon
            # the moment it comes back up.
            delay = base_delay_s * (2**attempt)
            delay += delay * random.uniform(0, 0.25)
            logger.debug(
                "[%s] transient error on attempt %d/%d; sleeping %.2fs: %s",
                label,
                attempt + 1,
                max_attempts,
                delay,
                exc,
            )
            time.sleep(delay)

    # Exhausted retries on transient errors. Raise the typed
    # transient exception so callers can distinguish "retry didn't
    # help" from "query was bad."
    raise FlightTransientError(
        f"{label} failed after {max_attempts} attempts: {last_exc}",
        cause=last_exc,
    ) from last_exc
