"""Public query surface — programmatic read access to runs, steps, measurements, events.

Mirrors what the operator UI's ``/results`` / ``/metrics`` /
``/explore`` / ``/events`` pages read. External tools, MCP clients,
data analysts, and custom dashboards reach for this module instead
of digging into ``litmus.analysis.*`` or ``litmus.data.*`` deep
paths.

Blessed pattern — construct once, reuse across cells or requests
(``close()`` and ``with`` are optional)::

    from litmus import queries

    q = queries.RunsQuery()
    recent = q.list_recent(limit=10)
    # ... later ...
    outcomes = q.count_by_outcome()
    # no close() needed — the analytical daemon is a separate process
    # that self-manages via PID-ref and idle timeout

``with`` is supported on all classes and releases the daemon ref
promptly when you want deterministic cleanup::

    with queries.RunsQuery() as q:
        recent = q.list_recent(limit=10)
        outcomes = q.count_by_outcome()

    with queries.MeasurementsQuery() as q:
        yields = q.yield_summary(group_by="uut_part_number")

``EventStore`` additionally offers ``get_shared()`` to share the
watcher thread across multiple callers in the same process::

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
from litmus.data._store import Store
from litmus.data.event_store import EventStore

__all__ = [
    "ColumnSchema",
    "EventStore",
    "FieldRef",
    "FieldRole",
    "MeasurementsQuery",
    "RunsQuery",
    "StepsQuery",
    "Store",
]
