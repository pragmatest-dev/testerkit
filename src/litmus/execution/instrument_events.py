"""Runner-neutral instrument-event emission.

Each runner emits ``InstrumentConnected`` events for the instruments
its session resolved. The shape is the same across runners — only
the source of the records differs (pytest reads
``get_instrument_records()`` from the active state; OpenHTF reads
its own plug registry, etc.).
"""

from __future__ import annotations

from typing import Any

from litmus.data.events import InstrumentConnected
from litmus.execution.run_scope import (
    RunScope,
    instrument_cal_fields,
    instrument_info_fields,
)
from litmus.models.instrument import InstrumentRecord


def emit_instrument_events(
    logger: RunScope,
    event_log: Any,
    records: dict[str, InstrumentRecord],
) -> None:
    """Emit one ``InstrumentConnected`` event per resolved instrument."""
    for role, rec in records.items():
        event = InstrumentConnected(
            session_id=logger._session_id,
            run_id=logger.test_run.id,
            role=role,
            instrument_id=rec.instrument_id,
            driver=rec.driver,
            resource=rec.resource,
            protocol=rec.protocol,
            **instrument_info_fields(rec),
            **instrument_cal_fields(rec),
            mocked=rec.mocked,
        )
        event_log.emit(event)
