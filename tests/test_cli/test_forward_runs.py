"""``testerkit forward --runs`` — unit tests (no live server; docs/36 P2).

Mirrors ``tests/test_cli/test_forward_channels_files.py``'s style: the HTTP
POST is monkeypatched, real run/events artifacts are written to a tmp dir
(via the real ``EventAccumulator``/``materialize_run_to_parquet`` pipeline +
hand-written WAL segments), and the pure ledger / dedup / retry logic is
exercised directly. No live server, no network — the cloud-side acceptance
of ``/ingest/runs`` is docs/36 P3, not P2 (see the module docstring's REVIEW
NEEDED note).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from testerkit.cli import forward_cmd
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.events import MeasurementRecorded, RunEnded, RunStarted, StepEnded, StepStarted
from testerkit.replication import EVENT_WAL_SCHEMA, read_new_run_artifacts

_T0 = datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC)


def _materialize_one_run(
    runs_root: Path, *, run_id: uuid.UUID, session_id: uuid.UUID, outcome: str = "passed"
) -> Path:
    acc = EventAccumulator()
    acc.on_event(
        RunStarted(session_id=session_id, run_id=run_id, occurred_at=_T0, uut_serial_number="SN1")
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
    acc.on_event(RunEnded(session_id=session_id, run_id=run_id, occurred_at=_T0, outcome=outcome))
    path = materialize_run_to_parquet(acc, runs_root, outcome=outcome, run_ended_at=_T0)
    assert path is not None
    return path


def _write_wal_segment(events_dir: Path, run_id: uuid.UUID, session_id: uuid.UUID) -> None:
    n = 2
    data: dict[str, list[object]] = {name: [None] * n for name in EVENT_WAL_SCHEMA.names}
    data["id"] = [f"e0-{run_id}", f"e1-{run_id}"]
    data["event_type"] = ["run.started", "run.ended"]
    data["occurred_at"] = [_T0] * n
    data["received_at"] = [_T0] * n
    data["session_id"] = [str(session_id)] * n
    data["run_id"] = [str(run_id)] * n
    data["writer_key"] = ["w0"] * n
    data["event_offset"] = [0, 1]
    data["json"] = ["{}"] * n
    table = pa.table(data, schema=EVENT_WAL_SCHEMA)
    seg_dir = events_dir / "2026-09-21"
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg = seg_dir / f"seg-{run_id}.arrow"
    with pa.OSFile(str(seg), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as w:
        w.write_table(table)


# --------------------------------------------------------------------------- #
# Cursor persistence                                                           #
# --------------------------------------------------------------------------- #


def test_runs_cursor_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    forward_cmd._save_runs_cursor(path, {("run-1", "hash-a")}, {"seg-key-1"})
    sent_runs, sent_events = forward_cmd._load_runs_cursor(path)
    assert sent_runs == {("run-1", "hash-a")}
    assert sent_events == {"seg-key-1"}


def test_runs_cursor_missing_file_is_empty(tmp_path: Path) -> None:
    sent_runs, sent_events = forward_cmd._load_runs_cursor(tmp_path / "nope.json")
    assert sent_runs == set()
    assert sent_events == set()


# --------------------------------------------------------------------------- #
# _forward_runs_once                                                           #
# --------------------------------------------------------------------------- #


def test_forward_runs_once_nothing_new_returns_none(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs" / "runs"
    events_dir = tmp_path / "events"
    runs_dir.mkdir(parents=True)
    events_dir.mkdir()

    def _boom(*a, **k):
        raise AssertionError("should not POST when there is nothing to forward")

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _boom)
    monkeypatch.setattr(forward_cmd, "_post_run_events", _boom)
    result = forward_cmd._forward_runs_once(
        runs_dir, events_dir, tmp_path / "r.json", "http://x", "tk", timeout=5.0
    )
    assert result is None


def test_forward_runs_once_posts_run_and_events_and_advances_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    events_dir = tmp_path / "events"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)
    _write_wal_segment(events_dir, run_id, session_id)

    posted_runs: list[str] = []
    posted_events: list[str] = []
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_parquet",
        lambda url, token, art, *, timeout: posted_runs.append(art.run_id) or {"accepted": True},
    )
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_events",
        lambda url, token, rid, table, *, timeout: posted_events.append(rid) or {"accepted": True},
    )

    cursor_path = tmp_path / "r.json"
    result = forward_cmd._forward_runs_once(
        runs_root / "runs", events_dir, cursor_path, "http://x", "tk", timeout=5.0
    )

    assert result == {"runs": 1, "events_artifacts": 1, "events_skipped_dupe": 0}
    assert posted_runs == [str(run_id)]
    assert posted_events == [str(run_id)]

    sent_runs, sent_events = forward_cmd._load_runs_cursor(cursor_path)
    assert len(sent_runs) == 1
    assert len(sent_events) == 1

    # Re-run: nothing new -- must not resend.
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_parquet",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resend run")),
    )
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_events",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resend events")),
    )
    assert (
        forward_cmd._forward_runs_once(
            runs_root / "runs", events_dir, cursor_path, "http://x", "tk", timeout=5.0
        )
        is None
    )


def test_forward_runs_once_no_events_for_run_still_forwards_run(
    tmp_path: Path, monkeypatch
) -> None:
    """A run whose WAL events have already been pruned (or never existed on
    this bench) still forwards its Parquet -- the events artifact is
    best-effort, never a blocker on the run artifact itself."""
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)
    # No WAL segment written for this run.

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", lambda *a, **k: {"accepted": True})
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_events",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no events to post")),
    )

    result = forward_cmd._forward_runs_once(
        runs_root / "runs", events_dir, tmp_path / "r.json", "http://x", "tk", timeout=5.0
    )
    assert result == {"runs": 1, "events_artifacts": 0, "events_skipped_dupe": 0}


def test_forward_runs_once_rematerialized_run_is_reforwarded(tmp_path: Path, monkeypatch) -> None:
    """docs/36 P2 done-when: a re-materialized run (same run_id, new content)
    replaces its prior upload -- the forwarder re-sends it, keyed on the NEW
    content hash."""
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id, outcome="aborted")

    posted_outcomes: list[str] = []

    def _fake_post_run(url, token, art, *, timeout):
        posted_outcomes.append(art.table.column("run_outcome").to_pylist()[0])
        return {"accepted": True}

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _fake_post_run)
    monkeypatch.setattr(forward_cmd, "_post_run_events", lambda *a, **k: {"accepted": True})

    cursor_path = tmp_path / "r.json"
    result1 = forward_cmd._forward_runs_once(
        runs_root / "runs", events_dir, cursor_path, "http://x", "tk", timeout=5.0
    )
    assert result1 is not None
    assert result1["runs"] == 1
    assert posted_outcomes == ["aborted"]

    # Re-materialize: a real completion supersedes the synthetic abort.
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id, outcome="passed")

    result2 = forward_cmd._forward_runs_once(
        runs_root / "runs", events_dir, cursor_path, "http://x", "tk", timeout=5.0
    )
    assert result2 is not None
    assert result2["runs"] == 1  # re-sent, not skipped
    assert posted_outcomes == ["aborted", "passed"]  # both versions actually went out


def test_forward_runs_once_does_not_advance_ledger_on_post_failure(
    tmp_path: Path, monkeypatch
) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)

    def _fail(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _fail)
    cursor_path = tmp_path / "r.json"
    with pytest.raises(OSError):
        forward_cmd._forward_runs_once(
            runs_root / "runs", events_dir, cursor_path, "http://x", "tk", timeout=5.0
        )

    sent_runs, sent_events = forward_cmd._load_runs_cursor(cursor_path)
    assert sent_runs == set()
    assert sent_events == set()


def test_forward_runs_once_persists_ledger_per_run(tmp_path: Path, monkeypatch) -> None:
    """The second of two runs fails to POST -- the first must already be
    durably recorded (persist-per-run, not batched at the end)."""
    runs_root = tmp_path / "runs"
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    r1, s1 = uuid.uuid4(), uuid.uuid4()
    r2, s2 = uuid.uuid4(), uuid.uuid4()
    _materialize_one_run(runs_root, run_id=r1, session_id=s1)
    _materialize_one_run(runs_root, run_id=r2, session_id=s2)

    calls: list[str] = []

    def _flaky_post(url, token, art, *, timeout):
        calls.append(art.run_id)
        if len(calls) == 2:
            raise OSError("network down")
        return {"accepted": True}

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _flaky_post)
    monkeypatch.setattr(forward_cmd, "_post_run_events", lambda *a, **k: {"accepted": True})

    cursor_path = tmp_path / "r.json"
    with pytest.raises(OSError):
        forward_cmd._forward_runs_once(
            runs_root / "runs", events_dir, cursor_path, "http://x", "tk", timeout=5.0
        )

    sent_runs, _sent_events = forward_cmd._load_runs_cursor(cursor_path)
    assert len(sent_runs) == 1  # only the first (successful) run was recorded


# --------------------------------------------------------------------------- #
# _forward_all_once wiring                                                     #
# --------------------------------------------------------------------------- #


def test_forward_all_once_default_flag_never_touches_runs(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    events_dir.mkdir()

    def _boom(*a, **k):
        raise AssertionError("must not be called when runs is disabled")

    monkeypatch.setattr(forward_cmd, "_forward_runs_once", _boom)
    monkeypatch.setattr(forward_cmd, "_post_ingest", _boom)

    result = forward_cmd._forward_all_once(
        events_dir,
        tmp_path / "e.json",
        tmp_path / "channels",
        tmp_path / "c.json",
        tmp_path / "files",
        tmp_path / "f.json",
        tmp_path / "runs",
        tmp_path / "r.json",
        "http://x",
        "tk",
        timeout=5.0,
        channels=False,
        files=False,
        runs=False,
    )
    assert result == {}


def test_forward_all_once_runs_enabled(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    events_dir.mkdir()

    monkeypatch.setattr(
        forward_cmd,
        "_forward_runs_once",
        lambda *a, **k: {"runs": 1, "events_artifacts": 1, "events_skipped_dupe": 0},
    )

    result = forward_cmd._forward_all_once(
        events_dir,
        tmp_path / "e.json",
        tmp_path / "channels",
        tmp_path / "c.json",
        tmp_path / "files",
        tmp_path / "f.json",
        tmp_path / "runs",
        tmp_path / "r.json",
        "http://x",
        "tk",
        timeout=5.0,
        channels=False,
        files=False,
        runs=True,
    )
    assert result == {"runs": {"runs": 1, "events_artifacts": 1, "events_skipped_dupe": 0}}


# --------------------------------------------------------------------------- #
# POST wire shape (Parquet content-type, URL shape) — mirrors                  #
# test_post_channel_segment_url_quotes_channel_id's style                      #
# --------------------------------------------------------------------------- #


def test_post_run_parquet_sends_parquet_content_type_and_bytes(tmp_path: Path, monkeypatch) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    path = _materialize_one_run(tmp_path / "runs", run_id=run_id, session_id=session_id)
    artifacts = read_new_run_artifacts(tmp_path / "runs" / "runs", sent=set())
    art = artifacts[0]

    captured = {}

    class _FakeResp:
        def read(self):
            return b'{"accepted": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["data"] = req.data
        return _FakeResp()

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", _fake_urlopen)
    result = forward_cmd._post_run_parquet("http://x", "tk", art, timeout=5.0)

    assert result == {"accepted": True}
    assert captured["url"] == "http://x/ingest/runs"
    assert captured["headers"]["Content-type"] == forward_cmd._PARQUET_CONTENT_TYPE
    assert captured["data"] == path.read_bytes()


def test_post_run_events_url_quotes_run_id(monkeypatch) -> None:
    import pyarrow as pa

    table = pa.table({"id": ["e0"]})
    captured = {}

    class _FakeResp:
        def read(self):
            return b'{"accepted": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        return _FakeResp()

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", _fake_urlopen)
    forward_cmd._post_run_events("http://x", "tk", "run/with slash", table, timeout=5.0)

    assert captured["url"] == "http://x/ingest/runs/run%2Fwith%20slash/events"
    assert captured["headers"]["Content-type"] == forward_cmd._PARQUET_CONTENT_TYPE
