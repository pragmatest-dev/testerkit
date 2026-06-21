"""Tests for observation pinning (#4 / #39).

Verifies that uut_pin flows from _auto_traceability → Observation event →
vector.observation_pins → at-rest outputs lane uut_pin → measurements_dynamic.uut_pin.

Test plan:
  (a) observe() inside an active connection lands uut_pin on the Observation event
      and mirrors onto vector.observation_pins.
  (b) observe() with no active connection yields uut_pin=None on the event and empty
      observation_pins on the vector.
  (c) encode_lane_structs with pins passes uut_pin into the lane struct; without pins
      uut_pin is None.
  (d) At-rest parquet written with pinned outputs lands uut_pin in
      measurements_dynamic after daemon ingest; plain outputs have NULL.
  (e) output_pins is excluded from the flat row dict (byte-stable output); uut_pin
      rides on the lane struct only.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data.backends._row_helpers import MeasurementRow, encode_lane_structs
from litmus.data.data_dir import resolve_data_dir
from litmus.data.events import Observation
from litmus.data.models import TestVector
from litmus.data.run_store import RunStore
from litmus.data.schemas import RUN_ROW_SCHEMA
from litmus.execution._state import (
    push_active_connection,
    push_current_vector,
    reset_active_connection,
    reset_current_vector,
)
from litmus.execution.harness import Context, TestHarness
from litmus.models.test_config import FixtureConnection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _FakeTestRun:
    id: UUID


class _FakeRunScope:
    def __init__(self, event_log: _FakeEventLog, run_id: UUID) -> None:
        self.event_log = event_log
        self.test_run = _FakeTestRun(id=run_id)


@pytest.fixture
def _event_log() -> _FakeEventLog:
    return _FakeEventLog()


@pytest.fixture
def _ctx_with_logger(_event_log: _FakeEventLog) -> Iterator[Context]:
    """Context wired with a fake run scope so observe() emits events."""
    session_id = uuid4()
    run_id = uuid4()
    harness = TestHarness(session_id=session_id)
    ctx = Context(harness=harness)
    scope = _FakeRunScope(event_log=_event_log, run_id=run_id)
    import litmus.execution.harness as harness_mod

    orig = harness_mod.get_current_run_scope
    harness_mod.get_current_run_scope = lambda: scope  # type: ignore[attr-defined]
    try:
        yield ctx
    finally:
        harness_mod.get_current_run_scope = orig  # type: ignore[attr-defined]


def _observation_events(log: _FakeEventLog) -> list[Observation]:
    return [e for e in log.events if isinstance(e, Observation)]


# ---------------------------------------------------------------------------
# (a) observe() with active connection stamps uut_pin on event and vector
# ---------------------------------------------------------------------------


def test_pin_on_event_and_vector_with_connection(
    _ctx_with_logger: Context, _event_log: _FakeEventLog
) -> None:
    conn = FixtureConnection(name="vout_measure", instrument="dmm", uut_pin="VOUT")
    vec = TestVector(index=0)
    t_vec = push_current_vector(vec)
    t_conn = push_active_connection(conn)
    try:
        _ctx_with_logger.observe("vout", 3.3)
    finally:
        reset_active_connection(t_conn)
        reset_current_vector(t_vec)

    events = _observation_events(_event_log)
    assert len(events) == 1
    assert events[0].uut_pin == "VOUT"
    assert vec.observation_pins.get("vout") == "VOUT"


def test_multiple_pins_all_stamped(_ctx_with_logger: Context, _event_log: _FakeEventLog) -> None:
    conn = FixtureConnection(name="vin_sense", instrument="dmm", uut_pin="VIN")
    vec = TestVector(index=0)
    t_vec = push_current_vector(vec)
    t_conn = push_active_connection(conn)
    try:
        _ctx_with_logger.observe("vin", 5.0)
        _ctx_with_logger.observe("vin_ripple", 0.01)
    finally:
        reset_active_connection(t_conn)
        reset_current_vector(t_vec)

    assert vec.observation_pins == {"vin": "VIN", "vin_ripple": "VIN"}
    events = _observation_events(_event_log)
    assert all(e.uut_pin == "VIN" for e in events)


# ---------------------------------------------------------------------------
# (b) observe() with no active connection → uut_pin = None / NULL
# ---------------------------------------------------------------------------


def test_no_connection_uut_pin_is_none(
    _ctx_with_logger: Context, _event_log: _FakeEventLog
) -> None:
    vec = TestVector(index=0)
    t_vec = push_current_vector(vec)
    try:
        _ctx_with_logger.observe("temp", 25.0)
    finally:
        reset_current_vector(t_vec)

    events = _observation_events(_event_log)
    assert len(events) == 1
    assert events[0].uut_pin is None
    assert vec.observation_pins == {}


# ---------------------------------------------------------------------------
# (c) encode_lane_structs passes uut_pin into lane struct
# ---------------------------------------------------------------------------


def test_encode_lane_with_pins() -> None:
    lanes = encode_lane_structs(
        {"vout": 3.3, "temp": 25.0},
        units={"vout": "V"},
        pins={"vout": "VOUT"},
    )
    by_name = {lane["name"]: lane for lane in lanes}
    assert by_name["vout"]["uut_pin"] == "VOUT"
    assert by_name["temp"]["uut_pin"] is None


def test_encode_lane_no_pins_uut_pin_null() -> None:
    lanes = encode_lane_structs({"vout": 3.3}, {"vout": "V"})
    assert lanes[0]["uut_pin"] is None


# ---------------------------------------------------------------------------
# (e) Flat row dict: output_pins excluded; uut_pin lives on the lane only
# ---------------------------------------------------------------------------


def test_flat_dict_no_output_pins_key() -> None:
    row = MeasurementRow(
        record_type="vector",
        run_id=str(uuid4()),
        session_id=str(uuid4()),
        uut_serial="SN-TEST",
        step_name="test_pin",
        step_index=0,
        vector_index=0,
        outputs={"vout": 3.3},
        output_units={"vout": "V"},
        output_pins={"vout": "VOUT"},
    )
    flat = row.to_flat_dict(at_rest=True)
    # output_pins must not appear as a top-level key
    assert "output_pins" not in flat
    # uut_pin rides on the lane struct inside the outputs list
    out_lanes = flat["outputs"]
    assert isinstance(out_lanes, list)
    vout_lane = next(lane for lane in out_lanes if lane["name"] == "vout")
    assert vout_lane["uut_pin"] == "VOUT"
    # No synthetic flat column
    assert "out_vout_uut_pin" not in flat
    assert "vout_uut_pin" not in flat


# ---------------------------------------------------------------------------
# (d) Daemon ingest: uut_pin in measurements_dynamic
# ---------------------------------------------------------------------------


def _make_vector_row(*, run_id: str, session_id: str, pins: dict[str, str]) -> dict:
    populated: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    populated.update(
        {
            "record_type": "vector",
            "run_id": run_id,
            "session_id": session_id,
            "run_started_at": datetime(2026, 6, 20, 10, 0, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 6, 20, 10, 1, 0, tzinfo=UTC),
            "run_outcome": "passed",
            "uut_serial": "SN-PIN-TEST",
            "station_id": "test-station",
            "step_name": "test_pin",
            "step_index": 0,
            "vector_index": 0,
            "vector_retry": 0,
            "outputs": encode_lane_structs(
                {"vout": 3.3, "temp": 25.0},
                units={"vout": "V"},
                pins=pins,
            ),
            "measurements": [],
        }
    )
    return populated


def _write_parquet(path: Path, row: dict) -> None:
    cols = {f.name: [row.get(f.name)] for f in RUN_ROW_SCHEMA}
    pq.write_table(pa.table(cols, schema=RUN_ROW_SCHEMA), path)


@pytest.fixture(scope="module")
def _pin_data() -> dict[str, str]:
    session_id = str(uuid4())
    run_pinned = str(uuid4())
    run_plain = str(uuid4())

    runs_dir = resolve_data_dir() / "runs" / "test-obs-pin"
    runs_dir.mkdir(parents=True, exist_ok=True)

    pq_pinned = runs_dir / f"{run_pinned}_SN-PIN.parquet"
    _write_parquet(
        pq_pinned,
        _make_vector_row(run_id=run_pinned, session_id=session_id, pins={"vout": "VOUT"}),
    )

    pq_plain = runs_dir / f"{run_plain}_SN-PLAIN.parquet"
    _write_parquet(
        pq_plain,
        _make_vector_row(run_id=run_plain, session_id=session_id, pins={}),
    )

    store = RunStore()
    store.notify_new_run(pq_pinned)
    store.notify_new_run(pq_plain)
    store.close()

    return {
        "session_id": session_id,
        "run_pinned": run_pinned,
        "run_plain": run_plain,
    }


def _query_eav(run_id: str) -> list[dict]:
    from litmus.data import runs_duckdb_manager
    from litmus.data._flight_query import FlightQueryClient

    runs_dir = resolve_data_dir() / "runs"
    location = runs_duckdb_manager.acquire(runs_dir)
    client = FlightQueryClient(location, "runs")
    return client.query(
        f"""
        SELECT name, unit, uut_pin
        FROM measurements_dynamic
        WHERE run_id = '{run_id}' AND role = 'output'
        ORDER BY name
        """
    )


def test_pinned_output_in_eav(_pin_data: dict[str, str]) -> None:
    rows = _query_eav(_pin_data["run_pinned"])
    by_name = {r["name"]: r for r in rows}
    assert "vout" in by_name, f"vout missing from EAV: {rows}"
    assert by_name["vout"]["uut_pin"] == "VOUT", f"Expected VOUT, got: {by_name['vout']}"
    assert by_name["temp"]["uut_pin"] is None


def test_plain_output_null_uut_pin_in_eav(_pin_data: dict[str, str]) -> None:
    rows = _query_eav(_pin_data["run_plain"])
    assert rows, "No EAV rows found for plain run"
    assert all(r["uut_pin"] is None for r in rows), f"Expected all NULL, got: {rows}"
