"""Unit tests for StepsQuery — list + tree shape from the steps table."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.analysis.steps_query import StepNode, StepRow, StepsQuery
from litmus.data.schemas import STEP_SCHEMA


def _step(
    *,
    run_id: str,
    session_id: str = "sess-1",
    started: datetime,
    step_index: int,
    step_name: str,
    step_path: str | None = None,
    outcome: str = "passed",
    measurement_count: int = 1,
    dut_serial: str = "SN001",
    station_id: str = "STA-01",
    slot_id: str | None = None,
) -> dict:
    """Build one step row in STEP_SCHEMA shape."""
    ended = started.replace(microsecond=0)
    populated: dict = {f.name: None for f in STEP_SCHEMA}
    populated.update(
        {
            "index": step_index,
            "name": step_name,
            "step_path": step_path or step_name,
            "outcome": outcome,
            "started_at": started,
            "ended_at": ended,
            "duration_s": 0.0,
            "has_measurements": measurement_count > 0,
            "measurement_count": measurement_count,
            "vector_count": 1,
            "run_id": run_id,
            "session_id": session_id,
            "slot_id": slot_id,
            "run_started_at": started,
            "run_ended_at": ended,
            "run_outcome": outcome,
            "dut_serial": dut_serial,
            "station_id": station_id,
        }
    )
    return populated


def _write_steps_file(runs_dir: Path, run_id: str, rows: list[dict]) -> Path:
    """Write a single ``_steps.parquet`` for a run."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    cols = {f.name: [r[f.name] for r in rows] for f in STEP_SCHEMA}
    path = runs_dir / f"{run_id}_steps.parquet"
    pq.write_table(pa.table(cols, schema=STEP_SCHEMA), path)
    return path


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """Two runs with a mix of flat and nested step paths.

    run-aaaaaaaa: flat steps step_0, step_1
    run-bbbbbbbb: nested step paths (power, power/voltage, power/current)
    """
    runs_dir = tmp_path / "runs"
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

    flat = [
        _step(
            run_id="run-aaaaaaaa-0001",
            started=base,
            step_index=0,
            step_name="step_0",
        ),
        _step(
            run_id="run-aaaaaaaa-0001",
            started=base,
            step_index=1,
            step_name="step_1",
            outcome="failed",
        ),
    ]
    _write_steps_file(runs_dir, "run-aaaaaaaa", flat)

    nested = [
        _step(
            run_id="run-bbbbbbbb-0002",
            started=base,
            step_index=0,
            step_name="power",
            step_path="power",
        ),
        _step(
            run_id="run-bbbbbbbb-0002",
            started=base,
            step_index=1,
            step_name="voltage",
            step_path="power/voltage",
        ),
        _step(
            run_id="run-bbbbbbbb-0002",
            started=base,
            step_index=2,
            step_name="current",
            step_path="power/current",
            outcome="failed",
        ),
    ]
    _write_steps_file(runs_dir, "run-bbbbbbbb", nested)

    return tmp_path


class TestListForRun:
    def test_returns_typed_rows(self, results_dir: Path):
        q = StepsQuery(_results_dir=results_dir)
        try:
            rows = q.list_for_run("run-aaaaaaaa")
        finally:
            q.close()
        assert len(rows) == 2
        assert all(isinstance(r, StepRow) for r in rows)
        assert [r.step_index for r in rows] == [0, 1]
        assert [r.step_name for r in rows] == ["step_0", "step_1"]
        assert [r.outcome for r in rows] == ["passed", "failed"]

    def test_id_prefix_match(self, results_dir: Path):
        """8-char prefix matches the full run id."""
        q = StepsQuery(_results_dir=results_dir)
        try:
            rows = q.list_for_run("run-aaaa")  # short prefix
        finally:
            q.close()
        assert len(rows) == 2
        assert all(r.run_id and r.run_id.startswith("run-aaaa") for r in rows)

    def test_unknown_run_returns_empty(self, results_dir: Path):
        q = StepsQuery(_results_dir=results_dir)
        try:
            rows = q.list_for_run("does-not-exist")
        finally:
            q.close()
        assert rows == []


class TestTreeForRun:
    def test_flat_steps_become_roots(self, results_dir: Path):
        q = StepsQuery(_results_dir=results_dir)
        try:
            tree = q.tree_for_run("run-aaaaaaaa")
        finally:
            q.close()
        assert len(tree) == 2
        assert all(isinstance(n, StepNode) for n in tree)
        assert [n.step.step_name for n in tree] == ["step_0", "step_1"]
        assert all(n.children == [] for n in tree)

    def test_nested_paths_build_tree(self, results_dir: Path):
        q = StepsQuery(_results_dir=results_dir)
        try:
            tree = q.tree_for_run("run-bbbbbbbb")
        finally:
            q.close()
        assert len(tree) == 1
        root = tree[0]
        assert root.step.step_path == "power"
        assert len(root.children) == 2
        assert {c.step.step_path for c in root.children} == {
            "power/voltage",
            "power/current",
        }


class TestListForSession:
    @pytest.fixture()
    def multi_slot_results_dir(self, tmp_path: Path) -> Path:
        """Two runs in one session, each with its own slot_id (multi-DUT timeline)."""
        runs_dir = tmp_path / "runs"
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        slot_a = [
            _step(
                run_id="run-aaaaaaaa-0001",
                session_id="sess-multi",
                slot_id="slot-A",
                started=base,
                step_index=0,
                step_name="warmup",
            ),
            _step(
                run_id="run-aaaaaaaa-0001",
                session_id="sess-multi",
                slot_id="slot-A",
                started=base,
                step_index=1,
                step_name="measure",
            ),
        ]
        slot_b = [
            _step(
                run_id="run-bbbbbbbb-0002",
                session_id="sess-multi",
                slot_id="slot-B",
                started=base,
                step_index=0,
                step_name="warmup",
                dut_serial="SN002",
            ),
        ]
        _write_steps_file(runs_dir, "run-aaaaaaaa", slot_a)
        _write_steps_file(runs_dir, "run-bbbbbbbb", slot_b)
        return tmp_path

    def test_returns_steps_across_session_siblings(self, multi_slot_results_dir: Path):
        q = StepsQuery(_results_dir=multi_slot_results_dir)
        try:
            rows = q.list_for_session("sess-multi")
        finally:
            q.close()
        assert len(rows) == 3
        assert {r.slot_id for r in rows} == {"slot-A", "slot-B"}
        slot_a_rows = [r for r in rows if r.slot_id == "slot-A"]
        assert [r.step_index for r in slot_a_rows] == [0, 1]


class TestDescribeColumns:
    def test_returns_table_columns(self, results_dir: Path):
        q = StepsQuery(_results_dir=results_dir)
        try:
            cols = q.describe_columns()
        finally:
            q.close()
        names = {c["column_name"] for c in cols}
        assert {"run_id", "step_index", "step_name", "step_path", "outcome"} <= names
