"""End-to-end tests for ``GET /api/runs/{run_id}`` typed composition.

Covers the load_run_view contract: run-level metadata from RunsQuery,
step list from StepsQuery, measurements from ParquetBackend. The
critical case: a run with steps but zero measurements must still
render its run header and step list (the bug fixed by the typed
refactor).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litmus.api.app import create_api_router
from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import (
    DUT,
    Measurement,
    Outcome,
    TestRun,
    TestStep,
    TestVector,
)


def _make_run(*, run_id, steps):
    """Build a TestRun. ``steps`` is a list of (name, vectors_or_None) tuples."""
    return TestRun(
        id=run_id,
        started_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 2, 12, 1, 0, tzinfo=UTC),
        dut=DUT(serial="SN-001"),
        outcome=Outcome.PASSED,
        steps=[
            TestStep(name=name, outcome=Outcome.PASSED, vectors=vectors or [])
            for name, vectors in steps
        ],
    )


@pytest.fixture
def client_with_run_factory(tmp_path, monkeypatch):
    """Factory that saves a run and returns ``(client, run_id)``."""
    results_root = tmp_path / "results"
    backend = ParquetBackend(results_dir=results_root)

    from litmus.models.project import ProjectConfig

    monkeypatch.setattr(
        "litmus.store.load_project_config",
        lambda *a, **kw: ProjectConfig(name="test", results_dir=str(results_root)),
    )

    def make(test_run: TestRun):
        backend.save_test_run(test_run)
        app = FastAPI()
        app.include_router(create_api_router())
        return TestClient(app), str(test_run.id)

    return make


class TestMeasurementLessRun:
    """A run whose steps recorded no measurements (setup-only / all-skipped)."""

    def test_run_metadata_renders(self, client_with_run_factory):
        run = _make_run(
            run_id=uuid4(),
            steps=[("setup", None), ("teardown", None)],
        )
        client, run_id = client_with_run_factory(run)

        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()

        assert body["run_id"].startswith(run_id[:8])
        assert body["dut_serial"] == "SN-001"
        assert body["outcome"] == "passed"
        assert body["started_at"] is not None

    def test_step_list_present(self, client_with_run_factory):
        run = _make_run(
            run_id=uuid4(),
            steps=[("setup", None), ("teardown", None)],
        )
        client, run_id = client_with_run_factory(run)

        body = client.get(f"/api/runs/{run_id}").json()
        step_names = [s["step_name"] for s in body["steps"]]
        assert step_names == ["setup", "teardown"]
        # Each step renders even though it has no measurements
        assert all(s["measurements"] == [] for s in body["steps"])


class TestMeasurementFullRun:
    """A run with measurements — measurements attach to their steps."""

    def test_measurements_under_correct_step(self, client_with_run_factory):
        run = _make_run(
            run_id=uuid4(),
            steps=[
                (
                    "voltage_check",
                    [
                        TestVector(
                            outcome=Outcome.PASSED,
                            measurements=[
                                Measurement(
                                    name="vout",
                                    value=3.3,
                                    outcome=Outcome.PASSED,
                                ),
                            ],
                        ),
                    ],
                ),
                ("teardown", None),
            ],
        )
        client, run_id = client_with_run_factory(run)

        body = client.get(f"/api/runs/{run_id}").json()
        steps = {s["step_name"]: s for s in body["steps"]}
        assert "voltage_check" in steps
        assert "teardown" in steps
        assert len(steps["voltage_check"]["measurements"]) == 1
        assert steps["voltage_check"]["measurements"][0]["measurement_name"] == "vout"
        assert steps["teardown"]["measurements"] == []


class TestUnknownRun:
    def test_returns_404(self, client_with_run_factory):
        # Create an unrelated run so the daemon is alive
        run = _make_run(run_id=uuid4(), steps=[("step", None)])
        client, _ = client_with_run_factory(run)
        resp = client.get("/api/runs/does-not-exist-id")
        assert resp.status_code == 404
