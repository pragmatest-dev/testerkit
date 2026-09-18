"""Tests for ``machine_id`` — the per-machine identity GUID.

Covers the accessor (``get_or_create_machine_id``), its stamping onto
``RunScope``/``TestRun`` and the run-row build helpers, and the additive
``machine_id`` column on the RUNS parquet schema (read-tolerant via
``union_by_name``, no schema-version bump).

Uses the canonical singleton runs daemon (``resolve_data_dir()`` / no
``_data_dir=tmp_path``) for the one test that exercises the real ingest
pipeline, per this repo's daemon-spawning convention (``tests/test_conventions.py``).
Everything else is pure-Python / pure-DuckDB and isolates via
``TESTERKIT_HOME=tmp_path`` (the machine-id file itself, not the runs data dir).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from testerkit.data.backends._row_helpers import build_run_metadata, build_run_row
from testerkit.data.data_dir import get_or_create_machine_id, resolve_data_dir
from testerkit.data.run_store import RunStore
from testerkit.data.schemas import RUN_ROW_SCHEMA
from testerkit.execution.run_scope import RunScope


def _isolate_machine_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the global-home resolution at a throwaway dir for this test.

    ``get_or_create_machine_id`` resolves the SAME global home
    ``resolve_data_dir()`` does (``TESTERKIT_HOME`` env var), so this never
    touches the real, shared ``machine_id`` file on the dev box / CI runner.
    """
    monkeypatch.setenv("TESTERKIT_HOME", str(tmp_path))


def test_get_or_create_machine_id_persists_and_is_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First call generates + persists a uuid4; later calls return the same value."""
    _isolate_machine_home(monkeypatch, tmp_path)

    first = get_or_create_machine_id()
    parsed = uuid.UUID(first)
    assert parsed.version == 4

    second = get_or_create_machine_id()
    assert second == first

    machine_id_path = tmp_path / "machine_id"
    assert machine_id_path.exists()
    assert machine_id_path.read_text(encoding="utf-8").strip() == first
    # Sibling of the global home's ``data`` dir, not inside it.
    assert machine_id_path.parent == tmp_path
    assert machine_id_path.parent != (tmp_path / "data")


def test_get_or_create_machine_id_stable_across_processes(tmp_path: Path) -> None:
    """A fresh process reading the same global home gets the same id back
    as the process that created it (real cross-process atomic-create check)."""
    env = dict(os.environ, TESTERKIT_HOME=str(tmp_path))
    code = (
        "from testerkit.data.data_dir import get_or_create_machine_id;"
        "print(get_or_create_machine_id())"
    )

    first = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert uuid.UUID(first)
    assert first == second


def test_get_or_create_machine_id_concurrent_first_run_does_not_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Many threads racing the very first call all converge on one id.

    Exercises the FileLock-guarded double-checked read-create-replace path —
    the on-disk file must end up holding exactly the id every caller got back.
    """
    _isolate_machine_home(monkeypatch, tmp_path)

    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        value = get_or_create_machine_id()
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 12
    assert len(set(results)) == 1  # every thread agrees
    assert (tmp_path / "machine_id").read_text(encoding="utf-8").strip() == results[0]


def test_run_scope_stamps_machine_id_from_accessor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RunScope sources TestRun.machine_id from the shared accessor, not
    from station config — same value the accessor returns directly."""
    _isolate_machine_home(monkeypatch, tmp_path)
    expected = get_or_create_machine_id()

    run_scope = RunScope(uut_serial="SN-MACHINE-ID-TEST", station_id=None)

    assert run_scope.test_run.machine_id == expected


def test_build_run_metadata_and_run_row_carry_machine_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """machine_id flows TestRun -> build_run_metadata -> RunParquetRow.to_flat_dict."""
    _isolate_machine_home(monkeypatch, tmp_path)
    run_scope = RunScope(uut_serial="SN-MACHINE-ID-TEST-2", station_id="station-x")

    run_context = build_run_metadata(run_scope.test_run)
    assert run_context["machine_id"] == run_scope.test_run.machine_id

    row = build_run_row(
        run_context=run_context,
        run_outcome=None,
        run_ended_at=None,
        instruments=[],
    )
    assert row["machine_id"] == run_scope.test_run.machine_id


def test_machine_id_column_null_fills_for_old_parquet_via_union_by_name(
    tmp_path: Path,
) -> None:
    """A pre-machine_id parquet (old schema) coexists with a current one under
    ``union_by_name=true`` — old rows null-fill, new rows carry the real value.
    No daemon involved — pure schema-mechanics check of the additive column.
    """
    old_schema = pa.schema([f for f in RUN_ROW_SCHEMA if f.name != "machine_id"])

    def _sentinel_row(schema: pa.Schema, run_id: str) -> dict[str, Any]:
        row: dict[str, Any] = dict.fromkeys(f.name for f in schema)
        row.update(
            {
                "record_type": "run",
                "run_id": run_id,
                "step_name": "",
                "step_index": 0,
                "step_path": "",
            }
        )
        return row

    old_row = _sentinel_row(old_schema, "old-run")
    old_path = tmp_path / "old.parquet"
    old_table = pa.table({f.name: [old_row[f.name]] for f in old_schema}, schema=old_schema)
    pq.write_table(old_table, old_path)

    new_row = _sentinel_row(RUN_ROW_SCHEMA, "new-run")
    new_row["machine_id"] = "11111111-1111-4111-8111-111111111111"
    new_path = tmp_path / "new.parquet"
    new_table = pa.table({f.name: [new_row[f.name]] for f in RUN_ROW_SCHEMA}, schema=RUN_ROW_SCHEMA)
    pq.write_table(new_table, new_path)

    conn = duckdb.connect()
    try:
        rows = conn.execute(
            "SELECT run_id, machine_id FROM read_parquet(?, union_by_name=true) ORDER BY run_id",
            [[str(old_path), str(new_path)]],
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("new-run", "11111111-1111-4111-8111-111111111111"),
        ("old-run", None),
    ]


def test_machine_id_stamped_on_a_run_reads_back_through_runs_daemon() -> None:
    """A run's machine_id survives parquet write -> daemon ingest -> ``runs`` read.

    Uses the canonical singleton runs daemon (``resolve_data_dir()``, no
    ``tmp_path``-scoped store) per this repo's daemon-spawning convention;
    isolation is by the uuid4 ``run_id``, not by directory.
    """
    run_id = str(uuid4())
    session_id = str(uuid4())
    machine_id = get_or_create_machine_id()

    row: dict[str, Any] = dict.fromkeys(f.name for f in RUN_ROW_SCHEMA)
    row.update(
        {
            "record_type": "run",
            "run_id": run_id,
            "session_id": session_id,
            "run_outcome": "passed",
            "run_started_at": datetime.now(UTC),
            "run_ended_at": datetime.now(UTC),
            "uut_serial_number": "SN-MACHINE-ID-DAEMON",
            "station_id": "station-machine-id-test",
            "machine_id": machine_id,
            "step_name": "",
            "step_index": 0,
            "step_path": "",
        }
    )

    runs_dir = resolve_data_dir() / "runs" / "test-machine-id"
    runs_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = runs_dir / f"{run_id}.parquet"
    pq.write_table(
        pa.table({f.name: [row[f.name]] for f in RUN_ROW_SCHEMA}, schema=RUN_ROW_SCHEMA),
        parquet_path,
    )

    store = RunStore()
    try:
        store.notify_new_run(parquet_path)
        rows = store._flight_query(f"SELECT machine_id FROM runs WHERE run_id = '{run_id}'")
    finally:
        store.close()

    assert rows
    assert rows[0]["machine_id"] == machine_id
