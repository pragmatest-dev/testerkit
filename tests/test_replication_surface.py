"""Pin the public ``testerkit.replication`` surface (GH-65).

Mirrors ``tests/test_queries_surface.py``: the re-exported symbols must be the
actual implementation objects (identity), and ``__all__`` must match exactly, so
the public surface can't silently drift from what it wraps.
"""

from __future__ import annotations

import testerkit.replication as replication
from testerkit.data._duckdb_flight_server import BatchDisposition as _BatchDisposition
from testerkit.data.event_log import _IPC_SCHEMA
from testerkit.data.event_log import EVENT_LOG_SCHEMA_VERSION as _ENVELOPE_VERSION
from testerkit.data.events import EVENT_CATALOG_VERSION as _CATALOG_VERSION


def test_replication_reexports_are_the_real_objects() -> None:
    assert replication.BatchDisposition is _BatchDisposition
    assert replication.EVENT_WAL_SCHEMA is _IPC_SCHEMA
    assert replication.EVENT_LOG_SCHEMA_VERSION is _ENVELOPE_VERSION
    assert replication.EVENT_CATALOG_VERSION is _CATALOG_VERSION


def test_replication_exposes_the_two_verbs() -> None:
    assert callable(replication.read_segments)
    assert callable(replication.ingest_replicated)


def test_replication_dunder_all_matches_actual_exports() -> None:
    assert set(replication.__all__) == {
        "BatchDisposition",
        "EVENT_CATALOG_VERSION",
        "EVENT_LOG_SCHEMA_VERSION",
        "EVENT_WAL_SCHEMA",
        "ingest_replicated",
        "read_segments",
    }
    # Every name in __all__ actually resolves on the module.
    for name in replication.__all__:
        assert hasattr(replication, name), name
