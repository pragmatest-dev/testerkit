"""Tests for ``services.get_run_detail``'s multi-site gate.

Change 4 of the site-model-consolidation contract: the "show the
parallel gantt?" decision moves off ``site_index`` null-ness (no
longer meaningful — site_index is always present) onto session→runs
fan-out. Uses the canonical singleton runs daemon, same pattern as
``tests/test_data/test_run_store.py``.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data.data_dir import resolve_data_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import RUN_ROW_SCHEMA
from litmus.ui.shared.services import get_run_detail


def _run_row(*, run_id: str, session_id: str, uut_serial: str, site_index: int) -> dict:
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "run",
            "run_id": run_id,
            "session_id": session_id,
            "site_index": site_index,
            "run_started_at": datetime(2026, 3, 10, 9, 0, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 3, 10, 9, 1, 0, tzinfo=UTC),
            "run_outcome": "passed",
            "uut_serial_number": uut_serial,
            "station_id": "station-detail",
        }
    )
    return populated


def _write_unified(path: Path, row: dict) -> None:
    cols = {f.name: [row.get(f.name)] for f in RUN_ROW_SCHEMA}
    pq.write_table(pa.table(cols, schema=RUN_ROW_SCHEMA), path)


@pytest.fixture
def runs_store() -> Generator[RunStore]:
    store = RunStore()
    yield store
    store.close()


def _canonical_dir(name: str) -> Path:
    d = resolve_data_dir() / "runs" / "test-run-detail-service" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_single_run_session_is_not_multi_site(runs_store: RunStore) -> None:
    session_id = str(uuid4())
    run_id = str(uuid4())
    runs_dir = _canonical_dir("single")
    pq_path = runs_dir / f"{run_id}_SN001.parquet"
    _write_unified(
        pq_path, _run_row(run_id=run_id, session_id=session_id, uut_serial="SN001", site_index=0)
    )
    runs_store.notify_new_run(pq_path)

    run, _steps, _measurements, is_multi_site = get_run_detail(run_id)

    assert run is not None
    assert run.site_index == 0
    assert is_multi_site is False


def test_two_runs_sharing_session_is_multi_site(runs_store: RunStore) -> None:
    session_id = str(uuid4())
    run_a = str(uuid4())
    run_b = str(uuid4())
    runs_dir = _canonical_dir("multi")
    pq_a = runs_dir / f"{run_a}_SN010.parquet"
    pq_b = runs_dir / f"{run_b}_SN011.parquet"
    _write_unified(
        pq_a, _run_row(run_id=run_a, session_id=session_id, uut_serial="SN010", site_index=0)
    )
    _write_unified(
        pq_b, _run_row(run_id=run_b, session_id=session_id, uut_serial="SN011", site_index=1)
    )
    runs_store.notify_new_run(pq_a)
    runs_store.notify_new_run(pq_b)

    run, _steps, _measurements, is_multi_site = get_run_detail(run_a)

    assert run is not None
    assert is_multi_site is True
