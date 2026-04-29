"""Tests for HDF5 subscriber."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("h5py")
import h5py  # pyright: ignore[reportMissingImports]

from litmus.data.exporters.hdf5 import Hdf5Subscriber
from litmus.data.models import TestRun


def _dataset(obj: object) -> h5py.Dataset:
    """Narrow h5py's Group|Dataset|Datatype union to a Dataset at read sites."""
    assert isinstance(obj, h5py.Dataset), f"expected Dataset, got {type(obj).__name__}"
    return obj


def _group(obj: object) -> h5py.Group:
    """Narrow h5py's Group|Dataset|Datatype union to a Group at read sites."""
    assert isinstance(obj, h5py.Group), f"expected Group, got {type(obj).__name__}"
    return obj


class TestHdf5Subscriber:
    """Test the event-driven subscriber path."""

    def _write_via_subscriber(
        self,
        test_run: TestRun,
        tmp_path: Path,
        replay: Callable[[TestRun, Any], None],
    ) -> Path:
        sub = Hdf5Subscriber(tmp_path)
        sub.open()
        replay(test_run, sub)
        sub.close()
        run_id = str(test_run.id)[:8]
        return tmp_path / "exports" / "hdf5" / f"{run_id}.hdf5"

    def test_creates_file(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        assert result.exists()

    def test_root_attrs(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            assert f.attrs["station_id"] == "station_alpha"
            assert f.attrs["dut_serial"] == "DUT-001"
            assert f.attrs["dut_part_number"] == "PN-200"
            assert f.attrs["test_phase"] == "qualification"
            assert f.attrs["operator_id"] == "OP-42"
            assert f.attrs["product_id"] == "PROD-100"

    def test_custom_metadata(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            assert f.attrs["custom_batch"] == "2026-Q1"
            assert f.attrs["custom_temperature"] == 25.0

    def test_step_hierarchy(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            assert "steps/power/output/voltage" in f
            grp = f["steps/power/output/voltage"]
            assert grp.attrs["name"] == "voltage_check"

    def test_measurement_values(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            vout = _dataset(f["steps/power/output/voltage/vectors/0/measurements/vout"])
            assert abs(float(vout[()]) - 3.30) < 0.01
            assert vout.attrs["units"] == "V"
            assert vout.attrs["comparator"] == "GELE"
            assert vout.attrs["limit_low"] == 3.0
            assert vout.attrs["limit_high"] == 3.6
            assert vout.attrs["outcome"] == "pass"
            assert vout.attrs["characteristic_id"] == "SPEC-001"
            assert vout.attrs["dut_pin"] == "VOUT"
            assert vout.attrs["instrument_name"] == "DMM_01"

    def test_inputs_as_vec_attrs(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """Event inputs dict → vector group attrs."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            vec0 = f["steps/power/output/voltage/vectors/0"]
            assert vec0.attrs["in_vin"] == 5.0
            assert vec0.attrs["in_load"] == 100.0

    def test_null_value_stored_as_nan(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            broken = _dataset(f["steps/power/protection/vectors/0/measurements/broken_sensor"])
            assert math.isnan(float(broken[()]))
            assert broken.attrs["value_missing"] == True  # noqa: E712

    def test_multiple_vectors(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """Step with 2 vectors has both in the hierarchy."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            vectors = _group(f["steps/power/output/voltage/vectors"])
            assert "0" in vectors
            assert "1" in vectors

    def test_nominal_comparator(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """EQ comparator measurement stores nominal attr."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        with h5py.File(result, "r") as f:
            vref = f["steps/power/output/voltage/vectors/1/measurements/vref_eq"]
            assert vref.attrs["comparator"] == "EQ"
            assert vref.attrs["nominal"] == 1.25
