"""Tests for RunStore — DuckDB-indexed query API over parquet files.

Uses the canonical singleton runs daemon (the only one this process
should ever talk to). Each fixture writes its synthetic parquets
under a unique subdirectory of the canonical runs dir with uuid4
``run_id`` / ``session_id`` so assertions filter cleanly past any
other tests' / users' runs in the shared store.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data.data_dir import resolve_data_dir
from litmus.data.ref import make_channel_uri
from litmus.data.run_store import RunStore
from litmus.data.schemas import RUN_ROW_SCHEMA


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _measurement_row(
    *,
    run_id: str,
    session_id: str,
    run_started_at: datetime,
    run_ended_at: datetime,
    run_outcome: str,
    dut_serial: str,
    station_id: str,
    step_name: str,
    step_index: int,
    measurement_name: str,
    measurement_value: float,
    measurement_outcome: str,
) -> dict:
    """One ``record_type='measurement'`` row in unified RUN_ROW_SCHEMA shape."""
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "measurement",
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": run_started_at,
            "run_ended_at": run_ended_at,
            "run_outcome": run_outcome,
            "dut_serial": dut_serial,
            "station_id": station_id,
            "step_name": step_name,
            "step_index": step_index,
            "step_path": step_name,
            "parent_path": "",
            "step_started_at": run_started_at,
            "step_ended_at": run_ended_at,
            "step_outcome": run_outcome,
            "step_vector_count": 1,
            "vector_index": 0,
            "vector_retry": 0,
            "measurement_name": measurement_name,
            "measurement_value": measurement_value,
            "measurement_outcome": measurement_outcome,
            "measurement_units": "V",
            "limit_low": 3.1,
            "limit_high": 3.5,
            "limit_nominal": 3.3,
        }
    )
    return populated


def _write_unified(path: Path, row: dict, *, extra_cols: dict | None = None) -> None:
    """Write a single-row unified parquet, with optional dynamic columns
    (e.g. ``out_waveform``) added alongside the schema fields."""
    cols = {f.name: [row[f.name]] for f in RUN_ROW_SCHEMA}
    schema_fields = list(RUN_ROW_SCHEMA)
    extra_fields: list = []
    if extra_cols:
        for name, value in extra_cols.items():
            cols[name] = [value]
            # Dynamic columns infer string for None, otherwise from value type.
            extra_fields.append(
                pa.field(
                    name,
                    pa.string() if value is None or isinstance(value, str) else pa.float64(),
                )
            )
    schema = pa.schema(schema_fields + extra_fields)
    pq.write_table(pa.table(cols, schema=schema), path)


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, str]:
    """Synthetic runs in the canonical store. Unique uuid identifiers."""
    session_id = str(uuid4())
    session_short = session_id[:8]
    run_001 = str(uuid4())
    run_002 = str(uuid4())

    canonical_runs = resolve_data_dir() / "runs" / "test-run-store"
    runs_dir = canonical_runs / "2026-03-01"
    runs_dir.mkdir(parents=True, exist_ok=True)

    uri = make_channel_uri("scope.ch1.waveform", session_id)

    pq1 = runs_dir / f"{run_001}_SN001.parquet"
    _write_unified(
        pq1,
        _measurement_row(
            run_id=run_001,
            session_id=session_id,
            run_started_at=_dt("2026-03-01T10:00:00Z"),
            run_ended_at=_dt("2026-03-01T10:05:00Z"),
            run_outcome="passed",
            dut_serial="SN001",
            station_id="station-1",
            step_name="test_voltage",
            step_index=0,
            measurement_name="voltage",
            measurement_value=3.3,
            measurement_outcome="passed",
        ),
        extra_cols={"out_waveform": uri},
    )

    pq2 = runs_dir / f"{run_002}_SN002.parquet"
    _write_unified(
        pq2,
        _measurement_row(
            run_id=run_002,
            session_id=session_id,
            run_started_at=_dt("2026-03-01T11:00:00Z"),
            run_ended_at=_dt("2026-03-01T11:05:00Z"),
            run_outcome="failed",
            dut_serial="SN002",
            station_id="station-1",
            step_name="test_voltage",
            step_index=0,
            measurement_name="voltage",
            measurement_value=2.8,
            measurement_outcome="failed",
        ),
        extra_cols={"out_waveform": None},
    )

    return {
        "session_id": session_id,
        "session_short": session_short,
        "run_001": run_001,
        "run_002": run_002,
        "pq1": str(pq1),
        "pq2": str(pq2),
    }


@pytest.fixture(scope="module")
def runs_store(fixture_data: dict[str, str]) -> Generator[RunStore]:
    """Canonical singleton RunStore — connects to the same daemon every other client uses."""
    store = RunStore()
    # Notify the canonical daemon of the synthetic parquets so it
    # ingests them into ``runs_materialized`` for the read-side tests.
    store.notify_new_run(Path(fixture_data["pq1"]))
    store.notify_new_run(Path(fixture_data["pq2"]))
    yield store
    store.close()


def test_get_run(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.get_run returns run details with prefix match."""
    run = runs_store.get_run(fixture_data["run_001"][:8])
    assert run is not None
    assert run.test_run_id == fixture_data["run_001"]
    assert run.dut_serial == "SN001"
    assert run.outcome == "passed"


def test_get_run_not_found(runs_store: RunStore) -> None:
    """RunStore.get_run returns None for unknown run_id."""
    assert runs_store.get_run("nonexistent-prefix-xxxxxxxx") is None


def test_find_run_file(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.find_run_file returns the parquet path."""
    f = runs_store.find_run_file(fixture_data["run_001"][:8])
    assert f is not None
    assert f.name == Path(fixture_data["pq1"]).name


def test_get_measurements(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.get_measurements returns measurement rows."""
    measurements = runs_store.get_measurements(fixture_data["run_001"][:8])
    assert len(measurements) == 1
    assert measurements[0]["measurement_name"] == "voltage"
    assert measurements[0]["measurement_value"] == 3.3


def test_find_channel_refs(runs_store: RunStore, fixture_data: dict[str, str]) -> None:
    """RunStore.find_channel_refs finds channel:// URIs in out_* columns."""
    refs = runs_store.find_channel_refs({fixture_data["session_short"]})
    assert any(
        r["channel_id"] == "scope.ch1.waveform"
        and r["session_short"] == fixture_data["session_short"]
        and r["col_name"] == "out_waveform"
        for r in refs
    ), f"expected scope.ch1.waveform ref for session {fixture_data['session_short']}, got {refs}"


def test_find_channel_refs_no_match(runs_store: RunStore) -> None:
    """No refs returned for unknown session shorts."""
    refs = runs_store.find_channel_refs({"deadbeef"})
    assert refs == []


def test_ref_dir_for() -> None:
    """ref_dir_for returns the _ref sidecar path."""
    p = Path("/data/runs/2026-03-01/test_run.parquet")
    assert RunStore.ref_dir_for(p) == Path("/data/runs/2026-03-01/test_run_ref")


def test_notify_new_run(runs_store: RunStore) -> None:
    """notify_new_run pushes a file path to the daemon for immediate indexing."""
    canonical_runs = resolve_data_dir() / "runs" / "test-run-store"
    runs_dir = canonical_runs / "2026-03-08"
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid4())
    session_id = str(uuid4())
    pq_file = runs_dir / f"{run_id}_SN099.parquet"
    _write_unified(
        pq_file,
        _measurement_row(
            run_id=run_id,
            session_id=session_id,
            run_started_at=_dt("2026-03-08T12:00:00Z"),
            run_ended_at=_dt("2026-03-08T12:01:00Z"),
            run_outcome="passed",
            dut_serial="SN099",
            station_id="station-2",
            step_name="test_current",
            step_index=0,
            measurement_name="current",
            measurement_value=1.5,
            measurement_outcome="passed",
        ),
    )

    runs_store.notify_new_run(pq_file)

    # The notify is idempotent + best-effort; verify the daemon picked
    # the parquet up by querying for the run_id.
    found = runs_store.get_run(run_id[:8])
    assert found is not None
    assert found.test_run_id == run_id
    assert found.dut_serial == "SN099"
