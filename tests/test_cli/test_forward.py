"""``testerkit forward`` — forwarder loop unit tests (no live server).

Exercises the pure logic: dropping the bench-local ``run.materialized`` signal so the
server re-derives, advancing the cursor only past accepted rows, and skipping rejected
ids. The HTTP POST is monkeypatched; a real WAL segment is written to a tmp dir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

from testerkit.cli import forward_cmd
from testerkit.replication import EVENT_WAL_SCHEMA


def _write_segment(path: Path, rows: list[dict]) -> None:
    n = len(rows)
    data: dict[str, list[object]] = {name: [None] * n for name in EVENT_WAL_SCHEMA.names}
    data["id"] = [r["id"] for r in rows]
    data["event_type"] = [r["event_type"] for r in rows]
    data["occurred_at"] = [datetime(2026, 9, 13, tzinfo=UTC)] * n
    data["session_id"] = ["s1"] * n
    data["writer_key"] = [r["writer_key"] for r in rows]
    data["event_offset"] = [r["event_offset"] for r in rows]
    data["json"] = ["{}"] * n
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(data, schema=EVENT_WAL_SCHEMA)
    with pa.OSFile(str(path), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as w:
        w.write_table(table)


def test_advance_cursor_skips_rejected() -> None:
    tbl = pa.table(
        {
            "id": ["a", "b", "c"],
            "writer_key": ["w0", "w0", "w1"],
            "event_offset": [0, 1, 0],
        }
    )
    out = forward_cmd._advance_cursor({}, tbl, rejected_ids={"b"})
    assert out == {"w0": 0, "w1": 0}  # w0 stops at 0 because b (offset 1) was rejected


def test_forward_once_drops_run_materialized_and_advances(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    _write_segment(
        events_dir / "2026-09-13" / "seg.arrow",
        [
            {"id": "e0", "writer_key": "w0", "event_offset": 0, "event_type": "run.started"},
            {"id": "e1", "writer_key": "w0", "event_offset": 1, "event_type": "run.materialized"},
            {"id": "e2", "writer_key": "w0", "event_offset": 2, "event_type": "run.ended"},
        ],
    )
    posted: dict = {}

    def _fake_post(url: str, token: str, body: bytes, *, timeout: float) -> dict:
        tbl = ipc.open_stream(pa.py_buffer(body)).read_all()
        posted["event_types"] = tbl.column("event_type").to_pylist()
        posted["ids"] = tbl.column("id").to_pylist()
        return {"inserted": tbl.num_rows, "deduped": 0, "rejected_ids": []}

    monkeypatch.setattr(forward_cmd, "_post_ingest", _fake_post)
    cursor_path = tmp_path / "cursor.json"
    disp = forward_cmd._forward_once(events_dir, cursor_path, "http://x", "tk_t", timeout=5.0)

    assert disp is not None and disp["inserted"] == 2
    assert "run.materialized" not in posted["event_types"]  # bench-local signal dropped
    assert set(posted["ids"]) == {"e0", "e2"}
    assert json.loads(cursor_path.read_text())["w0"] == 2  # advanced past accepted rows


def test_forward_once_nothing_new_returns_none(tmp_path: Path, monkeypatch) -> None:
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)

    def _boom(*a, **k):
        raise AssertionError("should not POST when there is nothing to forward")

    monkeypatch.setattr(forward_cmd, "_post_ingest", _boom)
    assert (
        forward_cmd._forward_once(events_dir, tmp_path / "c.json", "http://x", "t", timeout=5.0)
        is None
    )
