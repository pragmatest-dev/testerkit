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
        yields = q.yield_summary(group_by="uut_part_number")

``EventStore`` is the odd one out: it's a process-shared singleton
keyed by ``data_dir``, so there's no ``with`` — get the shared
instance and use it directly. The daemon stays open for the rest
of the process::

    store = queries.EventStore.get_shared()
    events = list(store.events(limit=100))

See:

- :class:`RunsQuery` — run-level queries (recent / by-uut / outcome
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

API note: ``MeasurementsQuery`` exposes a ``FieldRef``-based axis API
(``parametric``, ``histogram``, ``latest_run_limits``) for typed
field selection. ``RunsQuery`` and ``StepsQuery`` still use bare column
string APIs. Unification of the three onto a common FieldRef surface
is deferred.
"""

from __future__ import annotations

from litmus.analysis.measurement_facets import ColumnSchema, FieldRef, FieldRole
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.runs_query import RunsQuery
from litmus.analysis.steps_query import StepsQuery
from litmus.data.event_store import EventStore

__all__ = [
    "ColumnSchema",
    "EventStore",
    "FieldRef",
    "FieldRole",
    "MeasurementsQuery",
    "RunsQuery",
    "StepsQuery",
]
