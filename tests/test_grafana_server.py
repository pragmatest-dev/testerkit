"""Regression tests for the Grafana DuckDB views (``grafana/server.py``).

Schema 2.0 nests measurements under each vector row, so the ``measurements``
view (a plain ``SELECT *`` over the parquet) exposes the nested
``LIST<STRUCT>`` column, NOT one row per measurement. Measurement-centric
dashboards therefore query the flat ``measurement_values`` view, which
UNNESTs that list. These tests write a vector row with nested measurements
and assert the flat view exposes one row per measurement with the columns
the bundled dashboards reference.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.schemas import RUN_ROW_SCHEMA
from litmus.grafana.server import create_connection

_TS = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _measurement(name: str, value: float, outcome: str) -> dict:
    """One nested measurement struct (all _MEASUREMENT_STRUCT fields)."""
    return {
        "name": name,
        "value": value,
        "unit": "V",
        "outcome": outcome,
        "timestamp": _TS,
        "limit_low": 3.1,
        "limit_high": 3.5,
        "limit_nominal": 3.3,
        "limit_comparator": "GELE",
        "characteristic_id": None,
        "spec_ref": None,
        "uut_pin": None,
        "fixture_connection": None,
        "instrument_name": "dmm",
        "instrument_resource": None,
        "instrument_channel": None,
    }


def _write_vector_row(runs_dir: Path, measurements: list[dict]) -> None:
    """Write one ``record_type='vector'`` row with a nested measurements list."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    row: dict = {f.name: None for f in RUN_ROW_SCHEMA}
    row.update(
        {
            "record_type": "vector",
            "run_id": str(uuid4()),
            "session_id": str(uuid4()),
            "run_started_at": _TS,
            "run_ended_at": _TS,
            "run_outcome": "passed",
            "step_index": 0,
            "step_name": "test_rail",
            "step_path": "test_rail",
            "step_started_at": _TS,
            "step_ended_at": _TS,
            "vector_index": 0,
            "vector_started_at": _TS,
            "vector_ended_at": _TS,
            "uut_serial_number": "SN001",
            "uut_part_number": "PN-100",
            "part_id": "PN-100",
            "test_phase": "production",
            "measurements": measurements,
        }
    )
    cols = {f.name: [row[f.name]] for f in RUN_ROW_SCHEMA}
    pq.write_table(pa.table(cols, schema=RUN_ROW_SCHEMA), runs_dir / "run.parquet")


def test_measurement_values_unnests_one_row_per_measurement(tmp_path: Path) -> None:
    _write_vector_row(
        tmp_path / "runs" / "2026-06-25",
        [_measurement("vout", 3.3, "PASS"), _measurement("iout", 0.5, "FAIL")],
    )

    conn = create_connection(tmp_path)
    try:
        rows = conn.execute(
            "SELECT measurement_name, value, outcome, units, nominal, "
            "limit_low, limit_high, instrument_name "
            "FROM measurement_values ORDER BY measurement_name"
        ).fetchall()
    finally:
        conn.close()

    # The nested list of two measurements becomes two flat rows.
    assert [r[0] for r in rows] == ["iout", "vout"]
    by_name = {r[0]: r for r in rows}
    assert by_name["vout"][1] == 3.3  # value <- struct.value
    assert by_name["vout"][2] == "PASS"  # outcome <- struct.outcome
    assert by_name["vout"][3] == "V"  # units <- struct.unit
    assert by_name["vout"][4] == 3.3  # nominal <- struct.limit_nominal
    assert by_name["vout"][5] == 3.1  # limit_low
    assert by_name["iout"][2] == "FAIL"
    assert by_name["iout"][7] == "dmm"  # instrument_name


def test_measurement_values_exposes_dashboard_columns(tmp_path: Path) -> None:
    """Every column the bundled measurement dashboards query is present."""
    _write_vector_row(tmp_path / "runs", [_measurement("vout", 3.3, "PASS")])

    conn = create_connection(tmp_path)
    try:
        present = {
            d[0] for d in conn.execute("SELECT * FROM measurement_values LIMIT 0").description
        }
        # A representative measurement_distribution / failure_pareto filter.
        count_row = conn.execute(
            "SELECT count(*) FROM measurement_values WHERE measurement_name = 'vout'"
        ).fetchone()
    finally:
        conn.close()

    assert count_row is not None and count_row[0] == 1
    for col in (
        "measurement_name",
        "value",
        "units",
        "outcome",
        "nominal",
        "limit_low",
        "limit_high",
        "measurement_timestamp",
        "uut_serial_number",
        "test_phase",
        "part_id",
    ):
        assert col in present, f"dashboard column {col!r} missing from measurement_values"
