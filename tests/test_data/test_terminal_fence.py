"""Terminal fence — the spine-side half of P4.

Once a session is sealed (a ``SessionEnded`` landed), the events daemon rejects
post-seal PRODUCER writes (revival) before they're inserted, but lets daemon
COMPLETIONS through — a run's async ``RunMaterialized`` and a reaper ``RunEnded``
both carry ``derived=True`` and ride the fence so they still land after their
session has closed. Cheap fast-path: untouched unless a row targets a sealed
session.
"""

from __future__ import annotations

import pyarrow as pa

from testerkit.data._duckdb_daemon import _fence_post_seal, _json_is_derived


def _batch(rows: list[tuple[str, str]]) -> pa.Table:
    """rows = list of (session_id, json)."""
    return pa.table({"session_id": [r[0] for r in rows], "json": [r[1] for r in rows]})


# --------------------------------------------------------------------------- #
# _json_is_derived                                                            #
# --------------------------------------------------------------------------- #


def test_derived_true():
    assert _json_is_derived('{"derived": true}')


def test_derived_false_absent_malformed():
    assert not _json_is_derived('{"derived": false}')
    assert not _json_is_derived("{}")
    assert not _json_is_derived(None)
    assert not _json_is_derived("not json")


# --------------------------------------------------------------------------- #
# _fence_post_seal                                                            #
# --------------------------------------------------------------------------- #


def test_no_sealed_sessions_passes_everything():
    t = _batch([("s1", "{}"), ("s2", "{}")])
    out, rejected = _fence_post_seal(t, set())
    assert rejected == 0
    assert out.num_rows == 2


def test_post_seal_producer_write_rejected():
    t = _batch([("s1", '{"event_type": "test.measurement"}')])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == 1
    assert out.num_rows == 0


def test_post_seal_derived_completion_kept():
    # RunMaterialized / reaper RunEnded land after the seal — they carry derived.
    t = _batch([("s1", '{"event_type": "run.materialized", "derived": true}')])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == 0
    assert out.num_rows == 1


def test_unsealed_session_untouched():
    t = _batch([("s2", "{}")])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == 0
    assert out.num_rows == 1


def test_mixed_batch_drops_only_sealed_producer_rows():
    t = _batch(
        [
            ("s1", "{}"),  # sealed producer write → drop
            ("s1", '{"derived": true}'),  # sealed completion → keep
            ("s2", "{}"),  # unsealed → keep
        ]
    )
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == 1
    assert out.num_rows == 2
    # order preserved; the dropped row was the s1 producer write
    assert out.column("session_id").to_pylist() == ["s1", "s2"]
