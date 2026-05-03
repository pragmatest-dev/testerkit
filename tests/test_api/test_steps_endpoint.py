"""Tests for the ``/api/runs/{run_id}/steps[/tree]`` endpoints."""

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


def _make_run(*, run_id, step_specs):
    """Build a TestRun with one TestStep per ``step_specs`` entry."""
    return TestRun(
        id=run_id,
        started_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 2, 12, 1, 0, tzinfo=UTC),
        dut=DUT(serial="SN-001"),
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name=name,
                step_path=path,
                outcome=Outcome.PASSED,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        measurements=[
                            Measurement(name="m", value=1.0, outcome=Outcome.PASSED),
                        ],
                    ),
                ],
            )
            for name, path in step_specs
        ],
    )


@pytest.fixture
def client_with_nested_run(tmp_path, monkeypatch):
    """Save a run with a nested step_path tree."""
    run_id = uuid4()
    run = _make_run(
        run_id=run_id,
        step_specs=[
            ("power", "power"),
            ("voltage", "power/voltage"),
            ("current", "power/current"),
        ],
    )
    results_root = tmp_path / "results"
    backend = ParquetBackend(results_dir=results_root)
    backend.save_test_run(run)

    from litmus.models.project import ProjectConfig

    monkeypatch.setattr(
        "litmus.store.load_project_config",
        lambda *a, **kw: ProjectConfig(name="test", results_dir=str(results_root)),
    )

    app = FastAPI()
    app.include_router(create_api_router())
    return TestClient(app), str(run_id)


class TestGetSteps:
    def test_returns_typed_step_rows(self, client_with_nested_run):
        client, run_id = client_with_nested_run
        resp = client.get(f"/api/runs/{run_id}/steps")
        assert resp.status_code == 200
        steps = resp.json()["steps"]
        assert len(steps) == 3
        names = [s["step_name"] for s in steps]
        assert names == ["power", "voltage", "current"]
        # Pydantic-shaped fields, not raw dict from DuckDB
        assert all("file_path" in s and "step_index" in s for s in steps)

    def test_unknown_run_404(self, client_with_nested_run):
        client, _ = client_with_nested_run
        resp = client.get("/api/runs/does-not-exist/steps")
        assert resp.status_code == 404


class TestGetStepsTree:
    def test_builds_hierarchy(self, client_with_nested_run):
        client, run_id = client_with_nested_run
        resp = client.get(f"/api/runs/{run_id}/steps/tree")
        assert resp.status_code == 200
        tree = resp.json()["tree"]
        assert len(tree) == 1
        root = tree[0]
        assert root["step"]["step_path"] == "power"
        child_paths = {c["step"]["step_path"] for c in root["children"]}
        assert child_paths == {"power/voltage", "power/current"}

    def test_unknown_run_404(self, client_with_nested_run):
        client, _ = client_with_nested_run
        resp = client.get("/api/runs/does-not-exist/steps/tree")
        assert resp.status_code == 404
