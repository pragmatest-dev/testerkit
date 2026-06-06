"""Public query surface — programmatic read access to runs, steps, measurements, events.

Mirrors what the operator UI's ``/results`` / ``/metrics`` /
``/explore`` / ``/events`` pages read. External tools, MCP clients,
data analysts, and custom dashboards reach for this module instead
of digging into ``litmus.analysis.*`` or ``litmus.data.*`` deep
paths.

The three ``*Query`` classes manage their own Flight connection
lifecycle and should be used as context managers so the daemon
connection releases promptly::

    from litmus import queries

    with queries.RunsQuery() as q:
        recent = q.list_recent(limit=10)
        outcomes = q.count_by_outcome()

    with queries.MeasurementsQuery() as q:
        yields = q.yield_summary(group_by="dut_part_number")

``EventStore`` is the odd one out: it's a process-shared singleton
keyed by ``data_dir``, so there's no ``with`` — get the shared
instance and use it directly. The daemon stays open for the rest
of the process::

    store = queries.EventStore.get_shared()
    events = list(store.events(limit=100))

See:

- :class:`RunsQuery` — run-level queries (recent / by-dut / outcome
  distribution)
- :class:`StepsQuery` — step-level queries (by-run / step paths /
  per-step measurements)
- :class:`MeasurementsQuery` — measurement-level queries (yield /
  Cpk / distinct values / time-series)
- :class:`EventStore` — raw event timeline (session lifecycle,
  instrument reads, dialogs)

All four are also accessible via their original deep paths under
``litmus.analysis.*`` / ``litmus.data.event_store`` — those remain
the contributor-facing form. User-facing tools (docs, examples,
external scripts) should import from this module instead.
"""

from __future__ import annotations

from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.runs_query import RunsQuery
from litmus.analysis.steps_query import StepsQuery
from litmus.data.event_store import EventStore

__all__ = [
    "EventStore",
    "MeasurementsQuery",
    "RunsQuery",
    "StepsQuery",
]
