"""``testerkit forward --runs`` — unit tests (no live server; docs/36 P2).

Mirrors ``tests/test_cli/test_forward_channels_files.py``'s style: the HTTP
POST is monkeypatched, real run artifacts are written to a tmp dir (via the
real ``EventAccumulator``/``materialize_run_to_parquet`` pipeline), and the
pure ledger / dedup / retry logic is exercised directly. No live server, no
network.

The per-run compacted-events-artifact pipe (``/ingest/runs/{run_id}/events``,
``_post_run_events``) was removed 2026-09-23 (docs/42 §3.3) as redundant with
the main WAL forward (``/ingest/events``), which already carries every run's
events durably — see ``.tmp/ingest-drop-investigation.md`` (testerkit-server
repo) for the audit. This file now covers only the run-Parquet forward.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from testerkit.cli import forward_cmd
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.backends.parquet import materialize_run_to_parquet
from testerkit.data.events import MeasurementRecorded, RunEnded, RunStarted, StepEnded, StepStarted
from testerkit.replication import read_new_run_artifacts

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


# --------------------------------------------------------------------------- #
# Cursor persistence                                                           #
# --------------------------------------------------------------------------- #


def test_runs_cursor_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    forward_cmd._save_runs_cursor(path, {("run-1", "hash-a")})
    sent_runs = forward_cmd._load_runs_cursor(path)
    assert sent_runs == {("run-1", "hash-a")}


def test_runs_cursor_missing_file_is_empty(tmp_path: Path) -> None:
    sent_runs = forward_cmd._load_runs_cursor(tmp_path / "nope.json")
    assert sent_runs == set()


def test_runs_cursor_ignores_legacy_sent_events_key(tmp_path: Path) -> None:
    """A pre-2026-09-23 cursor file may still have a `sent_events` key from
    the now-removed events-artifact pipe — it's read (harmlessly ignored,
    never raises) and dropped on the next save."""
    import json

    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"sent_runs": [["run-1", "hash-a"]], "sent_events": ["seg-1"]}))
    sent_runs = forward_cmd._load_runs_cursor(path)
    assert sent_runs == {("run-1", "hash-a")}

    forward_cmd._save_runs_cursor(path, sent_runs)
    assert "sent_events" not in json.loads(path.read_text())


# --------------------------------------------------------------------------- #
# _forward_runs_once                                                           #
# --------------------------------------------------------------------------- #


def test_forward_runs_once_nothing_new_returns_none(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs" / "runs"
    runs_dir.mkdir(parents=True)

    def _boom(*a, **k):
        raise AssertionError("should not POST when there is nothing to forward")

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _boom)
    result = forward_cmd._forward_runs_once(
        runs_dir, tmp_path / "r.json", "http://x", "tk", timeout=5.0
    )
    assert result is None


def test_forward_runs_once_posts_run_and_advances_ledger(tmp_path: Path, monkeypatch) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)

    posted_runs: list[str] = []
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_parquet",
        lambda url, token, art, *, timeout: posted_runs.append(art.run_id) or {"accepted": True},
    )

    cursor_path = tmp_path / "r.json"
    result = forward_cmd._forward_runs_once(
        runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
    )

    assert result == {"runs": 1}
    assert posted_runs == [str(run_id)]

    sent_runs = forward_cmd._load_runs_cursor(cursor_path)
    assert len(sent_runs) == 1

    # Re-run: nothing new -- must not resend.
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_parquet",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resend run")),
    )
    assert (
        forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )
        is None
    )


def test_forward_runs_once_rematerialized_run_is_reforwarded(tmp_path: Path, monkeypatch) -> None:
    """docs/36 P2 done-when: a re-materialized run (same run_id, new content)
    replaces its prior upload -- the forwarder re-sends it, keyed on the NEW
    content hash."""
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id, outcome="aborted")

    posted_outcomes: list[str] = []

    def _fake_post_run(url, token, art, *, timeout):
        posted_outcomes.append(art.table.column("run_outcome").to_pylist()[0])
        return {"accepted": True}

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _fake_post_run)

    cursor_path = tmp_path / "r.json"
    result1 = forward_cmd._forward_runs_once(
        runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
    )
    assert result1 is not None
    assert result1["runs"] == 1
    assert posted_outcomes == ["aborted"]

    # Re-materialize: a real completion supersedes the synthetic abort.
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id, outcome="passed")

    result2 = forward_cmd._forward_runs_once(
        runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
    )
    assert result2 is not None
    assert result2["runs"] == 1  # re-sent, not skipped
    assert posted_outcomes == ["aborted", "passed"]  # both versions actually went out


def test_forward_runs_once_does_not_advance_ledger_on_post_failure(
    tmp_path: Path, monkeypatch
) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)

    def _fail(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _fail)
    cursor_path = tmp_path / "r.json"
    with pytest.raises(OSError):
        forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )

    sent_runs = forward_cmd._load_runs_cursor(cursor_path)
    assert sent_runs == set()


def test_forward_runs_once_persists_ledger_per_run(tmp_path: Path, monkeypatch) -> None:
    """The second of two runs fails to POST -- the first must already be
    durably recorded (persist-per-run, not batched at the end)."""
    runs_root = tmp_path / "runs"
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

    cursor_path = tmp_path / "r.json"
    with pytest.raises(OSError):
        forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )

    sent_runs = forward_cmd._load_runs_cursor(cursor_path)
    assert len(sent_runs) == 1  # only the first (successful) run was recorded


# --------------------------------------------------------------------------- #
# _forward_all_once wiring                                                     #
# --------------------------------------------------------------------------- #


def test_forward_all_once_no_runs_flag_skips_runs(tmp_path: Path, monkeypatch) -> None:
    """runs=False (the ``--no-runs`` LIMIT flag) skips the runs pass entirely.
    Runs forward ON by default; this exercises the opt-OUT path."""
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
        lambda *a, **k: {"runs": 1},
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
    assert result == {"runs": {"runs": 1}}


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


# --------------------------------------------------------------------------- #
# disposition handling (docs/42) -- conflict / rejected / missing            #
# --------------------------------------------------------------------------- #


def test_forward_runs_once_conflict_disposition_logs_writes_jsonl_and_advances(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)
    artifacts = read_new_run_artifacts(runs_root / "runs", sent=set())
    expected_hash = artifacts[0].content_hash

    calls: list[str] = []

    def _fake_post(url, token, art, *, timeout):
        calls.append(art.run_id)
        return {
            "disposition": "conflict",
            "run_id": art.run_id,
            "reason": "different file already exists for this run_id",
            "quarantined_as": f"quarantine/{art.run_id}.parquet",
        }

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _fake_post)

    cursor_path = tmp_path / "r.json"
    with caplog.at_level(logging.WARNING, logger="testerkit.forward"):
        result = forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )

    assert result == {"runs": 1}
    assert calls == [str(run_id)]

    # Warning logged naming run_id, disposition, reason, quarantined_as.
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert str(run_id) in msg
    assert "conflict" in msg
    assert "different file already exists for this run_id" in msg
    assert f"quarantine/{run_id}.parquet" in msg

    # jsonl line written with the expected fields.
    jsonl_path = cursor_path.parent / "_forward_conflicts.jsonl"
    lines = jsonl_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["run_id"] == str(run_id)
    assert record["disposition"] == "conflict"
    assert record["local_hash"] == expected_hash
    assert record["reason"] == "different file already exists for this run_id"
    assert record["quarantined_as"] == f"quarantine/{run_id}.parquet"
    assert record["server"] == "http://x"
    assert "ts" in record and record["ts"]

    # Cursor advanced (terminal outcome, not a retry).
    sent_runs = forward_cmd._load_runs_cursor(cursor_path)
    assert sent_runs == {(str(run_id), expected_hash)}

    # Re-run: must not resend / re-log / re-append.
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_parquet",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resend after conflict")),
    )
    assert (
        forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )
        is None
    )
    assert len(jsonl_path.read_text().splitlines()) == 1


def test_forward_runs_once_rejected_disposition_logs_writes_jsonl_and_advances(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)
    artifacts = read_new_run_artifacts(runs_root / "runs", sent=set())
    expected_hash = artifacts[0].content_hash

    def _fake_post(url, token, art, *, timeout):
        return {
            "disposition": "rejected",
            "run_id": art.run_id,
            "reason": "structurally invalid parquet",
            "quarantined_as": f"quarantine/{art.run_id}.parquet",
        }

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", _fake_post)

    cursor_path = tmp_path / "r.json"
    with caplog.at_level(logging.WARNING, logger="testerkit.forward"):
        result = forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )

    assert result == {"runs": 1}
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert str(run_id) in msg
    assert "rejected" in msg
    assert "structurally invalid parquet" in msg

    jsonl_path = cursor_path.parent / "_forward_conflicts.jsonl"
    lines = jsonl_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["disposition"] == "rejected"
    assert record["local_hash"] == expected_hash

    sent_runs = forward_cmd._load_runs_cursor(cursor_path)
    assert sent_runs == {(str(run_id), expected_hash)}


@pytest.mark.parametrize(
    "response",
    [
        {"accepted": True},  # older server -- no disposition field at all
        {"disposition": "accepted"},
        {"disposition": "duplicate"},
    ],
)
def test_forward_runs_once_non_conflict_disposition_is_unchanged(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture, response: dict
) -> None:
    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    _materialize_one_run(runs_root, run_id=run_id, session_id=session_id)
    artifacts = read_new_run_artifacts(runs_root / "runs", sent=set())
    expected_hash = artifacts[0].content_hash

    monkeypatch.setattr(forward_cmd, "_post_run_parquet", lambda *a, **k: dict(response))

    cursor_path = tmp_path / "r.json"
    with caplog.at_level(logging.WARNING, logger="testerkit.forward"):
        result = forward_cmd._forward_runs_once(
            runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0
        )

    assert result == {"runs": 1}
    assert caplog.records == []
    assert not (cursor_path.parent / "_forward_conflicts.jsonl").exists()

    sent_runs = forward_cmd._load_runs_cursor(cursor_path)
    assert sent_runs == {(str(run_id), expected_hash)}
