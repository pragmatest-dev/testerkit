"""``testerkit forward`` — events byte-budget chunking + ``--no-cursor`` mode.

Mirrors ``tests/test_cli/test_forward.py``'s style: the HTTP POST is
monkeypatched and a real WAL segment is written to a tmp dir. Exercises the two
store-and-forward changes:

* Change 1 — the events pass forwards in ascending, byte-bounded chunks (each
  POST body ≤ the budget), saving the cursor after EACH accepted chunk, so a
  large backlog can't 413 forever and a mid-drain failure keeps prior progress.
* Change 2 — ``--no-cursor`` reads every store's FULL set and never reads or
  writes a cursor file (correctness from server-side dedup).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from testerkit.cli import forward_cmd
from testerkit.replication import EVENT_WAL_SCHEMA, read_segments

_T0 = datetime(2026, 9, 13, tzinfo=UTC)


def _write_segment(
    path: Path,
    rows: list[tuple[str, str, int]],
    *,
    json_pad: int = 0,
    event_type: str = "step.ended",
) -> None:
    """Write a WAL segment. ``rows`` are ``(id, writer_key, event_offset)``;
    ``json_pad`` inflates each row's ``json`` payload so byte-budget chunking has
    something to bite on."""
    n = len(rows)
    data: dict[str, list[object]] = {name: [None] * n for name in EVENT_WAL_SCHEMA.names}
    data["id"] = [r[0] for r in rows]
    data["writer_key"] = [r[1] for r in rows]
    data["event_offset"] = [r[2] for r in rows]
    data["event_type"] = [event_type] * n
    data["occurred_at"] = [_T0] * n
    data["session_id"] = ["s1"] * n
    payload = json.dumps({"pad": "x" * json_pad}) if json_pad else "{}"
    data["json"] = [payload] * n
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(data, schema=EVENT_WAL_SCHEMA)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as w:
        w.write_table(table)


def _body_rows(body: bytes) -> list[tuple[str, int]]:
    tbl = ipc.open_stream(pa.py_buffer(body)).read_all()
    return list(
        zip(tbl.column("id").to_pylist(), tbl.column("event_offset").to_pylist(), strict=True)
    )


def _big_backlog(events_dir: Path, count: int, *, json_pad: int = 3000) -> None:
    _write_segment(
        events_dir / "2026-09-13" / "seg.arrow",
        [(f"e{i}", "w0", i) for i in range(count)],
        json_pad=json_pad,
    )


def _budget_for_a_few_rows(events_dir: Path, *, rows_per_chunk: float = 2.5) -> int:
    """A budget that fits ~``rows_per_chunk`` of the current backlog's rows,
    computed from the real single-row IPC size (robust to schema/framing
    overhead)."""
    table = read_segments(events_dir)
    assert table is not None
    one_row = len(forward_cmd._to_ipc_bytes(table.slice(0, 1)))
    return int(one_row * rows_per_chunk)


# --------------------------------------------------------------------------- #
# Change 1 — byte-budget chunking                                             #
# --------------------------------------------------------------------------- #


def test_backlog_is_forwarded_in_multiple_bounded_posts(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    _big_backlog(events_dir, 12)
    budget = _budget_for_a_few_rows(events_dir)

    bodies: list[bytes] = []

    def _fake_post(url, token, body, *, timeout):
        bodies.append(body)
        rows = _body_rows(body)
        return {"inserted": len(rows), "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _fake_post)
    cursor_path = tmp_path / "cursor.json"
    disp = forward_cmd._forward_once(
        events_dir, cursor_path, "http://x", "tk", timeout=5.0, max_bytes=budget
    )

    # Multiple POSTs, each bounded by the budget.
    assert len(bodies) > 1
    assert all(len(b) <= budget for b in bodies)
    # Every row delivered exactly once, in ascending offset order.
    delivered = [r for b in bodies for r in _body_rows(b)]
    assert [off for _id, off in delivered] == list(range(12))
    # Aggregate disposition + cursor advanced to the final offset.
    assert disp is not None and disp["inserted"] == 12
    assert json.loads(cursor_path.read_text())["w0"] == 11


def test_cursor_advances_after_each_chunk_and_tail_resends_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    events_dir = tmp_path / "events"
    _big_backlog(events_dir, 12)
    budget = _budget_for_a_few_rows(events_dir)
    cursor_path = tmp_path / "cursor.json"

    # Fail on the 3rd POST — chunks 1..2 are accepted and persisted first.
    pass1_delivered: list[tuple[str, int]] = []
    calls = {"n": 0}

    def _flaky_post(url, token, body, *, timeout):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("network down")
        pass1_delivered.extend(_body_rows(body))
        return {"inserted": len(_body_rows(body)), "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _flaky_post)
    with pytest.raises(OSError):
        forward_cmd._forward_once(
            events_dir, cursor_path, "http://x", "tk", timeout=5.0, max_bytes=budget
        )

    # Chunks 1..2 stayed persisted: cursor sits at the last accepted offset,
    # strictly before the end of the backlog.
    acked_max = max(off for _id, off in pass1_delivered)
    saved = json.loads(cursor_path.read_text())["w0"]
    assert saved == acked_max
    assert 0 <= saved < 11

    # Second pass: only the un-acked tail re-sends (nothing already-acked resent).
    pass2_delivered: list[tuple[str, int]] = []

    def _ok_post(url, token, body, *, timeout):
        pass2_delivered.extend(_body_rows(body))
        return {"inserted": len(_body_rows(body)), "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _ok_post)
    forward_cmd._forward_once(
        events_dir, cursor_path, "http://x", "tk", timeout=5.0, max_bytes=budget
    )

    assert [off for _id, off in pass2_delivered] == list(range(saved + 1, 12))
    # At-least-once: every offset delivered across the two passes (the failed
    # chunk-3 rows were never acked in pass 1, so they arrive in pass 2).
    all_offsets = {off for _id, off in pass1_delivered} | {off for _id, off in pass2_delivered}
    assert all_offsets == set(range(12))
    assert json.loads(cursor_path.read_text())["w0"] == 11


def test_small_delta_is_a_single_post(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-13" / "seg.arrow",
        [("e0", "w0", 0), ("e1", "w0", 1), ("e2", "w0", 2)],
    )
    posts = {"n": 0}

    def _fake_post(url, token, body, *, timeout):
        posts["n"] += 1
        return {"inserted": len(_body_rows(body)), "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _fake_post)
    # Default (16 MiB) budget — a tiny delta is one chunk = one POST.
    disp = forward_cmd._forward_once(events_dir, tmp_path / "c.json", "http://x", "tk", timeout=5.0)
    assert posts["n"] == 1
    assert disp is not None and disp["inserted"] == 3


def test_single_row_over_budget_is_still_sent_with_warning(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-13" / "seg.arrow",
        [("e0", "w0", 0), ("e1", "w0", 1)],
        json_pad=4000,
    )
    bodies: list[bytes] = []

    def _fake_post(url, token, body, *, timeout):
        bodies.append(body)
        return {"inserted": len(_body_rows(body)), "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _fake_post)
    # Budget below even a single row — each row must still ship as its own chunk.
    with caplog.at_level("WARNING", logger="testerkit.forward"):
        forward_cmd._forward_once(
            events_dir, tmp_path / "c.json", "http://x", "tk", timeout=5.0, max_bytes=100
        )
    assert [_body_rows(b) for b in bodies] == [[("e0", 0)], [("e1", 1)]]  # one row per POST
    assert any("over the" in r.message for r in caplog.records)
    assert json.loads((tmp_path / "c.json").read_text())["w0"] == 1  # still advanced


# --------------------------------------------------------------------------- #
# Budget resolution (constant / env / --max-bytes)                            #
# --------------------------------------------------------------------------- #


def test_resolve_max_bytes_default(monkeypatch) -> None:
    monkeypatch.delenv(forward_cmd._MAX_BYTES_ENV, raising=False)
    assert forward_cmd._resolve_max_bytes(None) == forward_cmd._DEFAULT_MAX_BYTES
    assert forward_cmd._DEFAULT_MAX_BYTES == 16 * 1024 * 1024


def test_resolve_max_bytes_env_override(monkeypatch) -> None:
    monkeypatch.setenv(forward_cmd._MAX_BYTES_ENV, "1048576")
    assert forward_cmd._resolve_max_bytes(None) == 1048576


def test_resolve_max_bytes_cli_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv(forward_cmd._MAX_BYTES_ENV, "1048576")
    assert forward_cmd._resolve_max_bytes(4096) == 4096


def test_resolve_max_bytes_invalid_env_falls_through(monkeypatch) -> None:
    monkeypatch.setenv(forward_cmd._MAX_BYTES_ENV, "not-a-number")
    assert forward_cmd._resolve_max_bytes(None) == forward_cmd._DEFAULT_MAX_BYTES


# --------------------------------------------------------------------------- #
# Change 2 — --no-cursor (stateless) mode                                     #
# --------------------------------------------------------------------------- #


def test_events_no_cursor_reads_full_set_and_writes_no_cursor(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-13" / "seg.arrow",
        [("e0", "w0", 0), ("e1", "w0", 1), ("e2", "w0", 2)],
    )
    cursor_path = tmp_path / "cursor.json"
    # An existing cursor that would skip EVERYTHING in normal mode, with a
    # sentinel key that survives iff the file is never rewritten.
    cursor_path.write_text(json.dumps({"w0": 2, "sentinel": "keep"}))

    posted: list[tuple[str, int]] = []

    def _fake_post(url, token, body, *, timeout):
        posted.extend(_body_rows(body))
        return {"inserted": len(_body_rows(body)), "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _fake_post)
    forward_cmd._forward_once(
        events_dir, cursor_path, "http://x", "tk", timeout=5.0, use_cursor=False
    )

    # Full set forwarded despite the existing cursor.
    assert [off for _id, off in posted] == [0, 1, 2]
    # Cursor file untouched — never read to skip, never rewritten.
    assert json.loads(cursor_path.read_text()) == {"w0": 2, "sentinel": "keep"}


def test_events_no_cursor_creates_no_cursor_file(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    _write_segment(events_dir / "2026-09-13" / "seg.arrow", [("e0", "w0", 0)])
    cursor_path = tmp_path / "cursor.json"

    monkeypatch.setattr(
        forward_cmd,
        "_post_ingest",
        lambda url, token, body, *, timeout: {"inserted": 1, "deduped": 0, "rejected_ids": []},
    )
    forward_cmd._forward_once(
        events_dir, cursor_path, "http://x", "tk", timeout=5.0, use_cursor=False
    )
    assert not cursor_path.exists()


def test_runs_no_cursor_reforwards_and_ignores_existing_ledger(tmp_path: Path, monkeypatch) -> None:
    from testerkit.data.backends._event_accumulator import EventAccumulator
    from testerkit.data.backends.parquet import materialize_run_to_parquet
    from testerkit.data.events import RunEnded, RunStarted
    from testerkit.replication import read_new_run_artifacts

    run_id, session_id = uuid.uuid4(), uuid.uuid4()
    runs_root = tmp_path / "runs"
    acc = EventAccumulator()
    acc.on_event(
        RunStarted(session_id=session_id, run_id=run_id, occurred_at=_T0, uut_serial_number="SN1")
    )
    acc.on_event(RunEnded(session_id=session_id, run_id=run_id, occurred_at=_T0, outcome="passed"))
    path = materialize_run_to_parquet(acc, runs_root, outcome="passed", run_ended_at=_T0)
    assert path is not None

    # A ledger that already contains this run (normal mode would skip it),
    # written raw with a sentinel that survives iff the file is never rewritten.
    art = read_new_run_artifacts(runs_root / "runs", sent=set())[0]
    cursor_path = tmp_path / "r.json"
    cursor_path.write_text(
        json.dumps({"sent_runs": [[art.run_id, art.content_hash]], "sentinel": "keep"})
    )

    posted_runs: list[str] = []
    monkeypatch.setattr(
        forward_cmd,
        "_post_run_parquet",
        lambda url, token, a, *, timeout: posted_runs.append(a.run_id) or {"accepted": True},
    )

    result = forward_cmd._forward_runs_once(
        runs_root / "runs", cursor_path, "http://x", "tk", timeout=5.0, use_cursor=False
    )
    # Re-forwarded despite the existing ledger; ledger file left untouched.
    assert result is not None and result["runs"] == 1
    assert posted_runs == [str(run_id)]
    assert json.loads(cursor_path.read_text()).get("sentinel") == "keep"


# --------------------------------------------------------------------------- #
# _forward_all_once threads use_cursor / max_bytes                            #
# --------------------------------------------------------------------------- #


def test_forward_all_once_threads_no_cursor_and_max_bytes(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def _spy(events_dir, cursor_path, url, token, *, timeout, max_bytes, use_cursor):
        captured["max_bytes"] = max_bytes
        captured["use_cursor"] = use_cursor
        return None

    monkeypatch.setattr(forward_cmd, "_forward_once", _spy)
    forward_cmd._forward_all_once(
        tmp_path / "events",
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
        max_bytes=4096,
        use_cursor=False,
    )
    assert captured == {"max_bytes": 4096, "use_cursor": False}
