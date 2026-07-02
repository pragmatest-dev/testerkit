"""ACCEPTANCE / VERIFICATION — step/vector grain model at-rest, real end-to-end.

Every permutation here is a REAL pytest subprocess run through the real
litmus plugin (same harness as ``test_class_step_containers.py``'s
``_run_pytest``), materialized through the REAL runs daemon, then read
back by globbing the actual parquet files on disk and querying them with
DuckDB directly — the at-rest ground truth. No ``_FakeLog``, no hand-built
``TestStep``/``TestVector``/``RunParquetRow``, no monkeypatched query.

The final test reads THROUGH ``StepsQuery.tree_for_run`` instead of the raw
parquet, to prove the query layer surfaces a swept step's vectors with their
own timing (the #24 fix — before it, vector rows were dropped by the default
``ended_at IS NOT NULL`` filter).

This is a one-off verification harness (not meant to be a permanent
regression suite) — run directly:

    uv run pytest tests/test_execution/test_grain_reshape_e2e_acceptance.py -s -v

Each test prints the raw at-rest rows it read so the run produces a
pasteable transcript, then asserts the expected shape from the grain
model doc (``docs/_internal/explorations/step-vector-grain-reshape.md``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from litmus.analysis.runs_query import RunsQuery
from litmus.data.backends._row_helpers import decode_lane_structs
from litmus.data.data_dir import resolve_data_dir


def _write_test(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))


def _run_pytest(test_file: Path, *, session_id: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "_LITMUS_SESSION_ID": session_id}
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _wait_for_run(session_id: str, *, timeout: float = 15.0) -> str:
    """Block until the run is FULLY materialized (ended_at set).

    Uses RunsQuery ONLY to learn identity/completion (run_id, ended_at) —
    never to read step/vector data. The actual step/vector rows below are
    read straight off the parquet files via DuckDB.
    """
    deadline = time.monotonic() + timeout
    runs_q = RunsQuery()
    try:
        while time.monotonic() < deadline:
            runs = runs_q.list_for_session(session_id)
            if runs:
                assert runs[0].run_id is not None
                return runs[0].run_id
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for session {session_id} run to materialize")
    finally:
        runs_q.close()


_COLUMNS = [
    "record_type",
    "step_path",
    "step_name",
    "step_retry",
    "vector_index",
    "vector_outer_index",
    "step_outcome",
    "vector_outcome",
    "run_outcome",
    "inputs",
    "outputs",
    "measurements",
]


def _read_raw_rows(session_id: str) -> list[dict[str, Any]]:
    """Glob the run data dir + DuckDB ``read_parquet`` — the REAL at-rest rows.

    Deliberately bypasses StepsQuery/list_for_run.
    """
    _wait_for_run(session_id)
    data_dir = resolve_data_dir()
    glob = str(data_dir / "runs" / "**" / "*.parquet")
    con = duckdb.connect()
    cols = ", ".join(_COLUMNS)
    query = f"""
        SELECT {cols}
        FROM read_parquet('{glob}', union_by_name=true)
        WHERE session_id = '{session_id}'
        ORDER BY record_type, step_path, step_retry, vector_outer_index, vector_index
    """
    rows = [dict(zip(_COLUMNS, r, strict=True)) for r in con.execute(query).fetchall()]
    con.close()
    return rows


def _print_rows(label: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n===== {label}: {len(rows)} raw at-rest rows =====")
    for r in rows:
        compact = {
            "record_type": r["record_type"],
            "step_path": r["step_path"],
            "step_retry": r["step_retry"],
            "vector_index": r["vector_index"],
            "vector_outer_index": r["vector_outer_index"],
            "step_outcome": r["step_outcome"],
            "vector_outcome": r["vector_outcome"],
            "inputs": decode_lane_structs(r["inputs"]),
            "outputs": decode_lane_structs(r["outputs"]),
            "measurements": [
                {"name": m["name"], "value": m["value"]} for m in (r["measurements"] or [])
            ],
        }
        print(json.dumps(compact, default=str))
    print(f"===== end {label} =====\n")


def _by_kind(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["record_type"], []).append(r)
    return out


# ---------------------------------------------------------------------------
# P1 — plain non-swept step.
# ---------------------------------------------------------------------------


def test_p1_plain_nonswept_step(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p1.py"
    _write_test(
        test_file,
        """\
        def test_x(measure):
            measure("vout", 3.3)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P1 plain non-swept step", rows)
    kinds = _by_kind(rows)

    assert "vector" not in kinds, "expected ZERO vector rows"
    steps = kinds["step"]
    assert len(steps) == 1, f"expected exactly ONE step row, got {len(steps)}"
    step = steps[0]
    assert step["vector_index"] is None
    meas_names = [m["name"] for m in step["measurements"]]
    assert meas_names == ["vout"], meas_names


# ---------------------------------------------------------------------------
# P3-Mode1 — leaf @parametrize (vectorized, runs N times). HEADLINE CLAIM.
# ---------------------------------------------------------------------------


def test_p3_mode1_leaf_parametrize(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p3m1.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.parametrize("vin", [1, 2, 3])
        def test_x(vin, measure):
            measure("vout", vin)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P3-Mode1 leaf @parametrize (headline: fused step + N vectors)", rows)
    kinds = _by_kind(rows)

    steps = kinds["step"]
    assert len(steps) == 1, f"expected exactly ONE fused step row, got {len(steps)}"
    step = steps[0]
    assert step["vector_index"] is None
    assert step["measurements"] == [], "step row should carry NO measurements (fused, empty)"

    vecs = sorted(kinds["vector"], key=lambda v: v["vector_index"])
    assert [v["vector_index"] for v in vecs] == [0, 1, 2]
    for v, expected_vin in zip(vecs, (1, 2, 3), strict=True):
        assert decode_lane_structs(v["inputs"])["vin"] == expected_vin
        assert [m["name"] for m in v["measurements"]] == ["vout"]
        assert v["measurements"][0]["value"] == expected_vin


# ---------------------------------------------------------------------------
# P3-Mode2 — in-body loop (vectors fixture). Two variants: pure loop, and
# pre-loop configure()+measure() to check ambient step-scope data.
# ---------------------------------------------------------------------------


def test_p3_mode2_inbody_loop_pure(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p3m2_pure.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"vin": [1, 2, 3]}])
        def test_x(vectors, measure):
            for v in vectors:
                measure("vout", v["vin"])
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P3-Mode2 in-body loop (pure, no pre-loop data)", rows)
    kinds = _by_kind(rows)

    steps = kinds["step"]
    assert len(steps) == 1
    step = steps[0]
    assert step["vector_index"] is None
    assert step["measurements"] == [], "pure loop: step row carries NO measurement"

    vecs = sorted(kinds["vector"], key=lambda v: v["vector_index"])
    assert [v["vector_index"] for v in vecs] == [0, 1, 2]
    for v, expected_vin in zip(vecs, (1, 2, 3), strict=True):
        assert decode_lane_structs(v["inputs"])["vin"] == expected_vin
        assert [m["name"] for m in v["measurements"]] == ["vout"]


def test_p3_mode2_inbody_loop_with_preloop_ambient(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p3m2_ambient.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"vin": [1, 2, 3]}])
        def test_x(vectors, measure, context):
            context.configure("setup_key", 99)
            measure("preflight", 1.0)
            for v in vectors:
                measure("vout", v["vin"])
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P3-Mode2 in-body loop (pre-loop configure()+measure() ambient)", rows)
    kinds = _by_kind(rows)

    steps = kinds["step"]
    assert len(steps) == 1
    step = steps[0]
    assert step["vector_index"] is None
    meas_names = [m["name"] for m in step["measurements"]]
    assert meas_names == ["preflight"], (
        f"pre-loop measure() should land on the step row, got {meas_names}"
    )
    assert decode_lane_structs(step["inputs"]).get("setup_key") == 99, (
        "pre-loop configure() should land in step row inputs"
    )

    vecs = sorted(kinds["vector"], key=lambda v: v["vector_index"])
    assert [v["vector_index"] for v in vecs] == [0, 1, 2]
    for v in vecs:
        assert [m["name"] for m in v["measurements"]] == ["vout"]


# ---------------------------------------------------------------------------
# P4-Mode1 — plain class + parametrized method.
# ---------------------------------------------------------------------------


def test_p4_mode1_plain_class_parametrized_method(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p4m1.py"
    _write_test(
        test_file,
        """\
        import pytest

        class TestC:
            @pytest.mark.parametrize("vin", [1, 2, 3])
            def test_m(self, vin, measure):
                measure("vout", vin)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P4-Mode1 plain class + parametrized method", rows)
    kinds = _by_kind(rows)
    steps = {s["step_path"]: s for s in kinds["step"]}
    assert set(steps) == {"TestC", "TestC/test_m"}, steps.keys()

    # Class container: its own step row, top-level, no vectors (C not swept).
    assert steps["TestC"]["vector_index"] is None
    c_vectors = [v for v in kinds.get("vector", []) if v["step_path"] == "TestC"]
    assert c_vectors == [], "unswept container must have ZERO vector rows"

    # Method: parent emitted no vectors -> method step row vector_index NULL,
    # but the method fuses to ONE step row + 3 vectors (its OWN parametrize).
    assert steps["TestC/test_m"]["vector_index"] is None
    assert steps["TestC/test_m"]["measurements"] == []
    m_vectors = sorted(
        (v for v in kinds.get("vector", []) if v["step_path"] == "TestC/test_m"),
        key=lambda v: v["vector_index"],
    )
    assert [v["vector_index"] for v in m_vectors] == [0, 1, 2]
    for v, expected_vin in zip(m_vectors, (1, 2, 3), strict=True):
        assert decode_lane_structs(v["inputs"])["vin"] == expected_vin
        assert [m["name"] for m in v["measurements"]] == ["vout"]


# ---------------------------------------------------------------------------
# P5 — @litmus_sweeps class + plain (unswept) method.
# ---------------------------------------------------------------------------


def test_p5_swept_class_plain_method(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p5.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestC:
            def test_m(self, voltage, measure):
                measure("vout", voltage)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P5 swept class (outer) + plain method (NOT fused)", rows)
    kinds = _by_kind(rows)
    steps_by_path: dict[str, list[dict[str, Any]]] = {}
    for s in kinds["step"]:
        steps_by_path.setdefault(s["step_path"], []).append(s)

    # Container: ONE fused step row + 3 leaf vectors (its own class-outer sweep).
    c_steps = steps_by_path["TestC"]
    assert len(c_steps) == 1, f"container should be ONE fused step row, got {len(c_steps)}"
    assert c_steps[0]["vector_index"] is None
    c_vectors = sorted(
        (v for v in kinds.get("vector", []) if v["step_path"] == "TestC"),
        key=lambda v: v["vector_index"],
    )
    assert [v["vector_index"] for v in c_vectors] == [0, 1, 2]
    assert {decode_lane_structs(v["inputs"])["voltage"] for v in c_vectors} == {1, 2, 3}

    # Method test_m has NO own sweep -> N SEPARATE step records (NOT fused),
    # one per vector_outer_index (the outer condition it ran under). ZERO
    # vector rows for the method itself.
    m_steps = steps_by_path["TestC/test_m"]
    assert len(m_steps) == 3, f"expected 3 NOT-fused step records for test_m, got {len(m_steps)}"
    outer_idxs = sorted(s["vector_outer_index"] for s in m_steps)
    assert outer_idxs == [0, 1, 2], outer_idxs
    for s in m_steps:
        assert s["vector_index"] is None
        assert [m["name"] for m in s["measurements"]] == ["vout"]
    m_vectors = [v for v in kinds.get("vector", []) if v["step_path"] == "TestC/test_m"]
    assert m_vectors == [], "unswept method must have ZERO vector rows of its own"


# ---------------------------------------------------------------------------
# P6 — @litmus_sweeps class (outer) + method with its OWN inner sweep.
# Two sub-variants: inner via @parametrize (Mode1) and inner via vectors
# fixture in-body loop (Mode2).
# ---------------------------------------------------------------------------


def test_p6_swept_class_parametrized_method_mode1(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p6m1.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestC:
            @pytest.mark.litmus_sweeps([{"current": [4, 5]}])
            def test_m(self, voltage, current, measure):
                measure("vout", voltage * current)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P6 swept class + Mode1 (@litmus_sweeps) inner method", rows)
    kinds = _by_kind(rows)
    steps_by_path: dict[str, list[dict[str, Any]]] = {}
    for s in kinds["step"]:
        steps_by_path.setdefault(s["step_path"], []).append(s)

    # Method has its OWN sweep -> N (outer) step records, NOT fused across
    # outer iterations, each with its own M inner vector rows (Mode1 variant
    # step rows share step_path but are separate execution rows, one per
    # (vector_outer_index) grain cell).
    m_steps = steps_by_path["TestC/test_m"]
    assert len(m_steps) == 3, f"expected 3 outer step records for test_m, got {len(m_steps)}"
    outer_idxs = sorted(s["vector_outer_index"] for s in m_steps)
    assert outer_idxs == [0, 1, 2], outer_idxs
    for s in m_steps:
        assert s["vector_index"] is None
        assert s["measurements"] == [], "fused step row carries no measurement itself"

    m_vectors = [v for v in kinds.get("vector", []) if v["step_path"] == "TestC/test_m"]
    assert len(m_vectors) == 6, f"expected 3 outer x 2 inner = 6 vector rows, got {len(m_vectors)}"
    pairs = set()
    for v in m_vectors:
        inp = decode_lane_structs(v["inputs"])
        assert "voltage" in inp and "current" in inp, inp
        pairs.add((inp["voltage"], inp["current"]))
        assert [m["name"] for m in v["measurements"]] == ["vout"]
    assert pairs == {(v, c) for v in (1, 2, 3) for c in (4, 5)}, pairs


def test_p6_swept_class_vectors_fixture_inner_mode2(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_p6m2.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.litmus_sweeps([{"voltage": [1, 2, 3]}])
        class TestC:
            @pytest.mark.litmus_sweeps([{"current": [4, 5]}])
            def test_m(self, voltage, vectors, measure):
                for v in vectors:
                    measure("vout", voltage * v["current"])
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("P6 swept class + Mode2 (vectors fixture) inner in-body loop", rows)
    kinds = _by_kind(rows)
    steps_by_path: dict[str, list[dict[str, Any]]] = {}
    for s in kinds["step"]:
        steps_by_path.setdefault(s["step_path"], []).append(s)

    # test_m runs as 3 pytest items (one per outer voltage) -> 3 step records,
    # NOT fused across the outer dimension (each is its own execution row),
    # each carrying its own 2 in-body inner vector rows (Mode2 loop).
    m_steps = steps_by_path["TestC/test_m"]
    assert len(m_steps) == 3, f"expected 3 outer step records for test_m, got {len(m_steps)}"
    outer_idxs = sorted(s["vector_outer_index"] for s in m_steps)
    assert outer_idxs == [0, 1, 2], outer_idxs
    for s in m_steps:
        assert s["vector_index"] is None
        assert s["measurements"] == []

    m_vectors = [v for v in kinds.get("vector", []) if v["step_path"] == "TestC/test_m"]
    assert len(m_vectors) == 6, f"expected 3 outer x 2 inner = 6 vector rows, got {len(m_vectors)}"
    pairs = set()
    for v in m_vectors:
        inp = decode_lane_structs(v["inputs"])
        assert "voltage" in inp and "current" in inp, inp
        pairs.add((inp["voltage"], inp["current"]))
    assert pairs == {(v, c) for v in (1, 2, 3) for c in (4, 5)}, pairs


# ---------------------------------------------------------------------------
# configure() I/O check — step-scope input lands on the right row.
# ---------------------------------------------------------------------------


def test_configure_io_lands_on_step_row(tmp_path: Path) -> None:
    session_id = str(uuid4())
    test_file = tmp_path / "test_cfg_io.py"
    _write_test(
        test_file,
        """\
        def test_x(context, measure):
            context.configure("vin", 12.0)
            measure("vout", 3.3)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("configure() I/O check (no sweep -> step-scope)", rows)
    kinds = _by_kind(rows)
    assert "vector" not in kinds
    steps = kinds["step"]
    assert len(steps) == 1
    step = steps[0]
    assert decode_lane_structs(step["inputs"]) == {"vin": 12.0}
    assert [m["name"] for m in step["measurements"]] == ["vout"]


def test_configure_io_lands_on_vector_row_when_swept(tmp_path: Path) -> None:
    """Same check, but under a parametrize sweep — configure() sets a NEW key
    (not the swept one) inside the parametrized body; the per-vector
    configure() lands on the leaf vector row, not the fused step row.
    """
    session_id = str(uuid4())
    test_file = tmp_path / "test_cfg_io_swept.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.parametrize("vin", [1, 2])
        def test_x(vin, context, measure):
            context.configure("trim", vin + 100)
            measure("vout", vin)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    rows = _read_raw_rows(session_id)
    _print_rows("configure() I/O check (swept -> vector-scope)", rows)
    kinds = _by_kind(rows)
    steps = kinds["step"]
    assert len(steps) == 1
    assert decode_lane_structs(steps[0]["inputs"]) == {}, (
        "fused step row should carry no per-variant configure() data"
    )
    vecs = sorted(kinds["vector"], key=lambda v: v["vector_index"])
    assert len(vecs) == 2
    for v, expected_vin in zip(vecs, (1, 2), strict=True):
        inp = decode_lane_structs(v["inputs"])
        assert inp.get("vin") == expected_vin, inp
        assert inp.get("trim") == expected_vin + 100, inp


# ---------------------------------------------------------------------------
# Query layer — #24: a swept step's vectors survive daemon materialization and
# surface through StepsQuery.tree_for_run with their OWN non-null timing (they
# were previously dropped by the default ``ended_at IS NOT NULL`` filter because
# the materializer nulled vector-bucket timestamps). This is the one test that
# reads THROUGH the query, not the raw parquet — closing the loop to StepsQuery.
# ---------------------------------------------------------------------------


def test_swept_vectors_visible_through_stepsquery(tmp_path: Path) -> None:
    from litmus.analysis.steps_query import StepsQuery

    session_id = str(uuid4())
    test_file = tmp_path / "test_sq_sweep.py"
    _write_test(
        test_file,
        """\
        import pytest

        @pytest.mark.parametrize("vin", [1, 2, 3])
        def test_x(vin, measure):
            measure("vout", vin)
        """,
    )
    result = _run_pytest(test_file, session_id=session_id)
    assert result.returncode == 0, result.stdout + result.stderr

    run_id = _wait_for_run(session_id)
    with StepsQuery() as q:
        tree = q.tree_for_run(run_id)

    assert len(tree) == 1, [n.step.step_path for n in tree]
    node = tree[0]
    assert node.step.step_path == "test_x"
    assert node.step.vector_index is None  # the step (ambient) carrier
    # The three sweep vectors are visible (pre-#24 they were filtered out) and
    # carry their OWN timing + outcome, not NULL.
    assert [v.vector_index for v in node.vectors] == [0, 1, 2]
    for v in node.vectors:
        assert v.started_at is not None, "vector must carry its own started_at (#24)"
        assert v.ended_at is not None, "vector must carry its own ended_at (#24)"
        assert v.outcome is not None, "vector must carry its own outcome (#24)"
