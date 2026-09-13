"""Terminal fence — the spine-side half of P4, plus the replication exemption.

Once a session is sealed (a ``SessionEnded`` landed), the events daemon rejects
post-seal PRODUCER writes (revival) before they're inserted, but lets EXEMPT rows
through — the daemon's own completions (a run's async ``RunMaterialized`` and a
reaper ``RunEnded``, both ``derived=True``) and replicated re-ingests
(``replicated=True``, already fenced at their source daemon). Both are trusted,
non-producer origin. Cheap fast-path: untouched unless a row targets a sealed
session. ``_fence_post_seal`` returns the kept table and the ids of rejected rows
(for the do_put disposition ack).
"""

from __future__ import annotations

import pyarrow as pa

from testerkit.data._duckdb_daemon import _fence_post_seal, _json_is_derived, _json_is_exempt


def _batch(rows: list[tuple[str, str]]) -> pa.Table:
    """rows = list of (session_id, json). Row ids are ``e0``, ``e1``, … in order,
    so rejection assertions can name the dropped rows. Shape mirrors the columns
    the fence reads from a real events table (``id`` / ``session_id`` / ``json``)."""
    return pa.table(
        {
            "id": [f"e{i}" for i in range(len(rows))],
            "session_id": [r[0] for r in rows],
            "json": [r[1] for r in rows],
        }
    )


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
# _json_is_exempt (derived OR replicated)                                     #
# --------------------------------------------------------------------------- #


def test_exempt_on_derived_or_replicated():
    assert _json_is_exempt('{"derived": true}')
    assert _json_is_exempt('{"replicated": true}')
    assert _json_is_exempt('{"derived": false, "replicated": true}')


def test_not_exempt_absent_false_malformed():
    assert not _json_is_exempt('{"derived": false, "replicated": false}')
    assert not _json_is_exempt("{}")
    assert not _json_is_exempt(None)
    assert not _json_is_exempt("not json")


# --------------------------------------------------------------------------- #
# _fence_post_seal                                                            #
# --------------------------------------------------------------------------- #


def test_no_sealed_sessions_passes_everything():
    t = _batch([("s1", "{}"), ("s2", "{}")])
    out, rejected = _fence_post_seal(t, set())
    assert rejected == []
    assert out.num_rows == 2


def test_post_seal_producer_write_rejected():
    t = _batch([("s1", '{"event_type": "test.measurement"}')])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == ["e0"]
    assert out.num_rows == 0


def test_post_seal_derived_completion_kept():
    # RunMaterialized / reaper RunEnded land after the seal — they carry derived.
    t = _batch([("s1", '{"event_type": "run.materialized", "derived": true}')])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == []
    assert out.num_rows == 1


def test_post_seal_replicated_event_kept():
    # A replicated re-ingest of a sealed session's own events rides through the
    # fence — it is not post-seal producer revival.
    t = _batch([("s1", '{"event_type": "test.measurement", "replicated": true}')])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == []
    assert out.num_rows == 1


def test_unsealed_session_untouched():
    t = _batch([("s2", "{}")])
    out, rejected = _fence_post_seal(t, {"s1"})
    assert rejected == []
    assert out.num_rows == 1


def test_mixed_batch_drops_only_sealed_producer_rows():
    t = _batch(
        [
            ("s1", "{}"),  # sealed producer write → drop
            ("s1", '{"derived": true}'),  # sealed completion → keep
            ("s1", '{"replicated": true}'),  # sealed replicated re-ingest → keep
            ("s2", "{}"),  # unsealed → keep
        ]
    )
    out, rejected = _fence_post_seal(t, {"s1"})
    # only the bare s1 producer row (e0) is dropped
    assert rejected == ["e0"]
    assert out.num_rows == 3
    # order preserved; the dropped row was the s1 producer write
    assert out.column("session_id").to_pylist() == ["s1", "s1", "s2"]
    assert out.column("id").to_pylist() == ["e1", "e2", "e3"]
