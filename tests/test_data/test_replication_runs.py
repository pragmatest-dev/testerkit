"""Unit tests for the run-Parquet / per-run-events forwarding readers
(docs/36 P2).

``read_new_run_artifacts``, ``read_run_events``, and ``run_events_segment_key``
are the sanctioned direct readers/identity-builders a forwarder uses to
discover finished runs and compact their events — the runs analogue of
``read_closed_channel_segments``/``read_new_file_records``. The HTTP/cursor-
persistence orchestration lives in ``testerkit.cli.forward_cmd`` and is tested
in ``tests/test_cli/test_forward_runs.py``.

Uses the real ``EventAccumulator``/``materialize_run_to_parquet`` pipeline (no
hand-rolled parquet) so the on-disk shape under test is authentic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from testerkit.replication import (
    EVENT_WAL_SCHEMA,
    read_new_run_artifacts,
    read_run_events,
    run_events_segment_key,
)

_T0 = datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC)


def _one_run_acc(
    run_id: uuid.UUID, session_id: uuid.UUID, *, serial: str = "SN1"
) -> EventAccumulator:
    acc = EventAccumulator()
    acc.on_event(
        RunStarted(session_id=session_id, run_id=run_id, occurred_at=_T0, uut_serial_number=serial)
    )
    acc.on_event(
        StepStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=_T0,
            step_path="a",
            step_name="a",
            step_index=0,
        )
    )
    acc.on_event(
        MeasurementRecorded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=_T0,
            step_name="a",
            step_index=0,
            step_path="a",
            measurement_name="v",
            value=1.0,
            outcome="passed",
        )
    )
    acc.on_event(
        StepEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=_T0,
            step_name="a",
            step_index=0,
            step_path="a",
            outcome="passed",
        )
    )
    acc.on_event(RunEnded(session_id=session_id, run_id=run_id, occurred_at=_T0, outcome="passed"))
    return acc


# --------------------------------------------------------------------------- #
# read_new_run_artifacts                                                      #
# --------------------------------------------------------------------------- #


def test_read_new_run_artifacts_reads_a_real_materialized_run(tmp_path: Path) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    acc = _one_run_acc(run_id, session_id)
    materialize_run_to_parquet(acc, tmp_path / "runs", outcome="passed", run_ended_at=_T0)

    artifacts = read_new_run_artifacts(tmp_path / "runs" / "runs", sent=set())
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.run_id == str(run_id)
    assert art.table.num_rows == 2  # run row + step row (no vector row here)
    assert len(art.content_hash) == 64  # sha256 hex


def test_read_new_run_artifacts_skips_already_sent(tmp_path: Path) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    acc = _one_run_acc(run_id, session_id)
    materialize_run_to_parquet(acc, tmp_path / "runs", outcome="passed", run_ended_at=_T0)

    runs_dir = tmp_path / "runs" / "runs"
    first = read_new_run_artifacts(runs_dir, sent=set())
    assert len(first) == 1
    sent = {(first[0].run_id, first[0].content_hash)}

    again = read_new_run_artifacts(runs_dir, sent=sent)
    assert again == []


def test_read_new_run_artifacts_rematerialized_run_is_not_already_sent(tmp_path: Path) -> None:
    """docs/36 P2 done-when: a re-materialized run (same run_id, new content)
    must NOT be skipped by a ledger keyed on the FIRST materialization's
    hash — this is the local analogue of the `#64` supersede: overwrite the
    SAME file path with different bytes."""
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    acc = _one_run_acc(run_id, session_id)
    materialize_run_to_parquet(acc, tmp_path / "runs", outcome="aborted", run_ended_at=_T0)

    runs_dir = tmp_path / "runs" / "runs"
    first = read_new_run_artifacts(runs_dir, sent=set())
    assert len(first) == 1
    assert first[0].table.column("run_outcome").to_pylist()[0] == "aborted"
    sent = {(first[0].run_id, first[0].content_hash)}

    # Re-materialize: a real completion supersedes the synthetic abort — same
    # run_id, same file path (started_at unchanged), DIFFERENT content.
    acc2 = _one_run_acc(run_id, session_id)
    path2 = materialize_run_to_parquet(acc2, tmp_path / "runs", outcome="passed", run_ended_at=_T0)
    first_path = first[0].path
    assert path2 == first_path  # same path — overwritten in place, not a new file

    again = read_new_run_artifacts(runs_dir, sent=sent)
    assert len(again) == 1  # NOT skipped — the hash changed
    assert again[0].run_id == str(run_id)
    assert again[0].content_hash != first[0].content_hash
    assert again[0].table.column("run_outcome").to_pylist()[0] == "passed"


def test_read_new_run_artifacts_skips_unparseable_file(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs" / "runs" / "2026-09-21"
    runs_dir.mkdir(parents=True)
    (runs_dir / "torn.parquet").write_bytes(b"not a real parquet file")

    assert read_new_run_artifacts(tmp_path / "runs" / "runs", sent=set()) == []


def test_read_new_run_artifacts_multiple_runs(tmp_path: Path) -> None:
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    materialize_run_to_parquet(
        _one_run_acc(r1, s1, serial="SN1"), tmp_path / "runs", outcome="passed", run_ended_at=_T0
    )
    materialize_run_to_parquet(
        _one_run_acc(r2, s2, serial="SN2"), tmp_path / "runs", outcome="failed", run_ended_at=_T0
    )

    artifacts = read_new_run_artifacts(tmp_path / "runs" / "runs", sent=set())
    assert {a.run_id for a in artifacts} == {str(r1), str(r2)}


# --------------------------------------------------------------------------- #
# read_run_events / run_events_segment_key                                    #
# --------------------------------------------------------------------------- #


def _wal_table(rows: list[dict[str, object]]) -> pa.Table:
    n = len(rows)
    data: dict[str, list[object]] = {name: [None] * n for name in EVENT_WAL_SCHEMA.names}
    data["id"] = [r["id"] for r in rows]
    data["event_type"] = [r.get("event_type", "test.measurement") for r in rows]
    data["occurred_at"] = [_T0] * n
    data["received_at"] = [_T0] * n
    data["session_id"] = [r["session_id"] for r in rows]
    data["run_id"] = [r["run_id"] for r in rows]
    data["writer_key"] = [r["writer_key"] for r in rows]
    data["event_offset"] = [r["event_offset"] for r in rows]
    data["json"] = [r.get("json", "{}") for r in rows]
    return pa.table(data, schema=EVENT_WAL_SCHEMA)


def _write_segment(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as w:
        w.write_table(table)


def test_read_run_events_filters_to_one_run_across_segments(tmp_path: Path) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-21" / "seg0.arrow",
        _wal_table(
            [
                {
                    "id": "e0",
                    "run_id": "run-a",
                    "session_id": "s1",
                    "writer_key": "w0",
                    "event_offset": 0,
                },
                {
                    "id": "e1",
                    "run_id": "run-b",
                    "session_id": "s2",
                    "writer_key": "w0",
                    "event_offset": 1,
                },
            ]
        ),
    )
    _write_segment(
        events_dir / "2026-09-21" / "seg1.arrow",
        _wal_table(
            [
                {
                    "id": "e2",
                    "run_id": "run-a",
                    "session_id": "s1",
                    "writer_key": "w1",
                    "event_offset": 0,
                }
            ]
        ),
    )

    table = read_run_events(events_dir, "run-a")
    assert table is not None
    assert table.column("id").to_pylist() == ["e0", "e2"]


def test_read_run_events_unknown_run_is_none(tmp_path: Path) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-21" / "seg0.arrow",
        _wal_table(
            [
                {
                    "id": "e0",
                    "run_id": "run-a",
                    "session_id": "s1",
                    "writer_key": "w0",
                    "event_offset": 0,
                }
            ]
        ),
    )
    assert read_run_events(events_dir, "run-nonexistent") is None


def test_read_run_events_empty_dir_is_none(tmp_path: Path) -> None:
    (tmp_path / "events").mkdir()
    assert read_run_events(tmp_path / "events", "run-a") is None


def test_run_events_segment_key_deterministic_for_same_coverage() -> None:
    table = _wal_table(
        [
            {
                "id": "e0",
                "run_id": "run-a",
                "session_id": "s1",
                "writer_key": "w0",
                "event_offset": 0,
            },
            {
                "id": "e1",
                "run_id": "run-a",
                "session_id": "s1",
                "writer_key": "w0",
                "event_offset": 1,
            },
        ]
    )
    key1 = run_events_segment_key("run-a", table)
    key2 = run_events_segment_key("run-a", table)
    assert key1 == key2


def test_run_events_segment_key_differs_for_wider_coverage() -> None:
    """A re-materialized run's events artifact covers a WIDER offset range
    (the real terminal lands at a higher offset than the synthetic abort's) —
    a genuinely different key, docs/36 P2's segment-key analogue of the run
    artifact's (run_id, content_hash) pair."""
    narrow = _wal_table(
        [{"id": "e0", "run_id": "run-a", "session_id": "s1", "writer_key": "w0", "event_offset": 0}]
    )
    wider = _wal_table(
        [
            {
                "id": "e0",
                "run_id": "run-a",
                "session_id": "s1",
                "writer_key": "w0",
                "event_offset": 0,
            },
            {
                "id": "e1",
                "run_id": "run-a",
                "session_id": "s1",
                "writer_key": "w0",
                "event_offset": 1,
            },
        ]
    )
    assert run_events_segment_key("run-a", narrow) != run_events_segment_key("run-a", wider)


def test_run_events_segment_key_differs_for_different_run_id() -> None:
    table = _wal_table(
        [{"id": "e0", "run_id": "run-a", "session_id": "s1", "writer_key": "w0", "event_offset": 0}]
    )
    assert run_events_segment_key("run-a", table) != run_events_segment_key("run-b", table)
