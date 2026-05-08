"""Unit tests for StepsQuery — list + tree shape from the steps table.

Uses the canonical singleton runs daemon. Synthetic steps go into
``canonical/runs/test-steps-query/`` with uuid4 run/session ids so
assertions filter cleanly past any other tests' / users' rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.analysis.steps_query import StepNode, StepRow, StepsQuery
from litmus.data.results_dir import resolve_results_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import RUN_ROW_SCHEMA


def _step(
    *,
    run_id: str,
    session_id: str,
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
    """Build one ``record_type='step'`` row in unified RUN_ROW_SCHEMA shape."""
    ended = started.replace(microsecond=0)
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "step",
            "step_index": step_index,
            "step_name": step_name,
            "step_path": step_path or step_name,
            "parent_path": "",
            "step_started_at": started,
            "step_ended_at": ended,
            "step_outcome": outcome,
            "step_vector_count": 1,
            "vector_index": 0,
            # measurement_name None → step-summary row (no measurement);
            # measurement_count is computed from row count downstream.
            "measurement_name": None,
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
    """Write the unified per-run parquet and return its path."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    cols = {f.name: [r[f.name] for r in rows] for f in RUN_ROW_SCHEMA}
    path = runs_dir / f"{run_id}.parquet"
    pq.write_table(pa.table(cols, schema=RUN_ROW_SCHEMA), path)
    return path


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, str]:
    """Two runs (flat + nested step paths) under a unique session.

    Single fixture per file → one acquire/release of the canonical
    daemon for the whole module.
    """
    session_id = str(uuid4())
    run_flat = str(uuid4())
    run_nested = str(uuid4())

    canonical_runs = resolve_results_dir() / "runs" / "test-steps-query"
    runs_dir = canonical_runs
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

    flat = [
        _step(
            run_id=run_flat,
            session_id=session_id,
            started=base,
            step_index=0,
            step_name="step_0",
        ),
        _step(
            run_id=run_flat,
            session_id=session_id,
            started=base,
            step_index=1,
            step_name="step_1",
            outcome="failed",
        ),
    ]
    flat_path = _write_steps_file(runs_dir, run_flat, flat)

    nested = [
        _step(
            run_id=run_nested,
            session_id=session_id,
            started=base,
            step_index=0,
            step_name="power",
            step_path="power",
        ),
        _step(
            run_id=run_nested,
            session_id=session_id,
            started=base,
            step_index=1,
            step_name="voltage",
            step_path="power/voltage",
        ),
        _step(
            run_id=run_nested,
            session_id=session_id,
            started=base,
            step_index=2,
            step_name="current",
            step_path="power/current",
            outcome="failed",
        ),
    ]
    nested_path = _write_steps_file(runs_dir, run_nested, nested)

    notifier = RunStore()
    try:
        notifier.notify_new_run(flat_path.with_name(f"{run_flat}.parquet"))
        notifier.notify_new_run(nested_path.with_name(f"{run_nested}.parquet"))
    finally:
        notifier.close()

    return {
        "session_id": session_id,
        "run_flat": run_flat,
        "run_nested": run_nested,
    }


class TestListForRun:
    def test_returns_typed_rows(self, fixture_data: dict[str, str]):
        with StepsQuery() as q:
            rows = q.list_for_run(fixture_data["run_flat"])
        assert len(rows) == 2
        assert all(isinstance(r, StepRow) for r in rows)
        assert [r.step_index for r in rows] == [0, 1]
        assert [r.step_name for r in rows] == ["step_0", "step_1"]
        assert [r.outcome for r in rows] == ["passed", "failed"]

    def test_id_prefix_match(self, fixture_data: dict[str, str]):
        """8-char prefix matches the full run id."""
        with StepsQuery() as q:
            rows = q.list_for_run(fixture_data["run_flat"][:8])
        assert len(rows) == 2
        run_id = fixture_data["run_flat"]
        assert all(r.run_id == run_id for r in rows)

    def test_unknown_run_returns_empty(self):
        with StepsQuery() as q:
            rows = q.list_for_run("does-not-exist-xxxxxxxx")
        assert rows == []


class TestTreeForRun:
    def test_flat_steps_become_roots(self, fixture_data: dict[str, str]):
        with StepsQuery() as q:
            tree = q.tree_for_run(fixture_data["run_flat"])
        assert len(tree) == 2
        assert all(isinstance(n, StepNode) for n in tree)
        assert [n.step.step_name for n in tree] == ["step_0", "step_1"]
        assert all(n.children == [] for n in tree)

    def test_nested_paths_build_tree(self, fixture_data: dict[str, str]):
        with StepsQuery() as q:
            tree = q.tree_for_run(fixture_data["run_nested"])
        assert len(tree) == 1
        root = tree[0]
        assert root.step.step_path == "power"
        assert len(root.children) == 2
        assert {c.step.step_path for c in root.children} == {
            "power/voltage",
            "power/current",
        }


class TestListForSession:
    """Multi-slot session: two sibling runs sharing one ``session_id``."""

    @pytest.fixture(scope="class")
    def multi_slot_data(self) -> dict[str, str]:
        """Two runs (different slots) sharing one unique session."""
        session_id = str(uuid4())
        run_a = str(uuid4())
        run_b = str(uuid4())

        canonical_runs = resolve_results_dir() / "runs" / "test-steps-query-multi"
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        slot_a_steps = [
            _step(
                run_id=run_a,
                session_id=session_id,
                slot_id="slot-A",
                started=base,
                step_index=0,
                step_name="warmup",
            ),
            _step(
                run_id=run_a,
                session_id=session_id,
                slot_id="slot-A",
                started=base,
                step_index=1,
                step_name="measure",
            ),
        ]
        slot_b_steps = [
            _step(
                run_id=run_b,
                session_id=session_id,
                slot_id="slot-B",
                started=base,
                step_index=0,
                step_name="warmup",
                dut_serial="SN002",
            ),
        ]
        path_a = _write_steps_file(canonical_runs, run_a, slot_a_steps)
        path_b = _write_steps_file(canonical_runs, run_b, slot_b_steps)

        notifier = RunStore()
        try:
            notifier.notify_new_run(path_a.with_name(f"{run_a}.parquet"))
            notifier.notify_new_run(path_b.with_name(f"{run_b}.parquet"))
        finally:
            notifier.close()

        return {
            "session_id": session_id,
            "run_a": run_a,
            "run_b": run_b,
        }

    def test_returns_steps_across_session_siblings(self, multi_slot_data: dict[str, str]):
        with StepsQuery() as q:
            rows = q.list_for_session(multi_slot_data["session_id"])
        assert len(rows) == 3
        assert {r.slot_id for r in rows} == {"slot-A", "slot-B"}
        slot_a_rows = [r for r in rows if r.slot_id == "slot-A"]
        assert [r.step_index for r in slot_a_rows] == [0, 1]


class TestDescribeColumns:
    def test_returns_table_columns(self):
        with StepsQuery() as q:
            cols = q.describe_columns()
        names = {c["column_name"] for c in cols}
        assert {"run_id", "step_index", "step_name", "step_path", "outcome"} <= names
