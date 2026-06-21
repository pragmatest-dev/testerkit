"""``litmus.queries`` re-exports the four programmatic-read entry points.

External tools (MCP clients, data analysts, custom dashboards) reach
for ``from litmus import queries`` instead of the deep
``litmus.analysis.*`` / ``litmus.data.event_store`` paths. This test
pins the surface so a future rename of the underlying module doesn't
silently break consumers.
"""

from __future__ import annotations

import litmus.queries as queries
from litmus.analysis.measurement_facets import ColumnSchema as _CS
from litmus.analysis.measurement_facets import FieldRef as _FR
from litmus.analysis.measurement_facets import FieldRole as _FRole
from litmus.analysis.measurements_query import MeasurementsQuery as _MQ
from litmus.analysis.runs_query import RunsQuery as _RQ
from litmus.analysis.steps_query import StepsQuery as _SQ
from litmus.data.event_store import EventStore as _ES


def test_queries_surface_exposes_read_entry_points() -> None:
    assert queries.RunsQuery is _RQ
    assert queries.StepsQuery is _SQ
    assert queries.MeasurementsQuery is _MQ
    assert queries.EventStore is _ES


def test_queries_surface_exposes_measurement_selector_types() -> None:
    assert queries.FieldRef is _FR
    assert queries.FieldRole is _FRole
    assert queries.ColumnSchema is _CS


def test_queries_dunder_all_matches_actual_exports() -> None:
    assert set(queries.__all__) == {
        "RunsQuery",
        "StepsQuery",
        "MeasurementsQuery",
        "EventStore",
        "FieldRef",
        "FieldRole",
        "ColumnSchema",
    }
