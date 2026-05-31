"""Tests for the /api/runs/{run_id}/ref endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from litmus.api.app import _serialize_ref, create_api_router
from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import (
    DUT,
    Measurement,
    Outcome,
    TestRun,
    TestStep,
    TestVector,
    Waveform,
)

# --- _serialize_ref unit tests -----------------------------------------------


class TestSerializeRef:
    """Type-dispatch logic for ref payloads."""

    def test_waveform_returns_model_dump(self) -> None:
        wfm = Waveform(t0=0.0, dt=0.001, Y=[1.0, 2.0, 3.0], attributes={"units": "V"})
        result = _serialize_ref(wfm)
        assert result == {
            "t0": 0.0,
            "dt": 0.001,
            "Y": [1.0, 2.0, 3.0],
            "attributes": {"units": "V"},
        }

    def test_arbitrary_basemodel_returns_model_dump(self) -> None:
        class Demo(BaseModel):
            x: int
            name: str

        result = _serialize_ref(Demo(x=42, name="foo"))
        assert result == {"x": 42, "name": "foo"}

    def test_dict_passes_through(self) -> None:
        d = {"key": "value", "nested": {"a": 1}}
        assert _serialize_ref(d) == d

    def test_png_bytes_get_image_content_type(self) -> None:
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        result = _serialize_ref(png)
        assert isinstance(result, Response)
        assert result.media_type == "image/png"
        assert result.body == png

    def test_pdf_bytes_get_pdf_content_type(self) -> None:
        pdf = b"%PDF-1.4\n%%EOF"
        result = _serialize_ref(pdf)
        assert isinstance(result, Response)
        assert result.media_type == "application/pdf"

    def test_text_bytes_get_text_content_type(self) -> None:
        text = b"hello world\n"
        result = _serialize_ref(text)
        assert isinstance(result, Response)
        assert result.media_type == "text/plain"

    def test_unknown_bytes_fall_back_to_octet_stream(self) -> None:
        binary = b"\x00\x01\x02\x03random"
        result = _serialize_ref(binary)
        assert isinstance(result, Response)
        assert result.media_type == "application/octet-stream"

    def test_numpy_ndarray(self) -> None:
        np = pytest.importorskip("numpy")
        arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
        result = _serialize_ref(arr)
        assert result == {
            "shape": [2, 2],
            "dtype": "float64",
            "data": [[1.0, 2.0], [3.0, 4.0]],
        }

    def test_pyarrow_table(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"a": [1, 2], "b": ["x", "y"]})
        result = _serialize_ref(tbl)
        assert result == {"data": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]}

    def test_unsupported_type_raises_415(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _serialize_ref(object())
        assert exc_info.value.status_code == 415
        assert "object" in exc_info.value.detail


# --- Integration tests via TestClient ---------------------------------------


@pytest.fixture
def app_with_run():
    """Save a TestRun with ref-typed observations to canonical.

    Per-test isolation is by uuid4 ``run_id`` — API endpoints query
    by id, so other tests' canonical rows don't leak in.
    """
    from litmus.data.data_dir import resolve_data_dir
    from litmus.data.run_store import RunStore

    run = TestRun(
        id=uuid4(),
        started_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 2, 12, 1, 0, tzinfo=UTC),
        dut=DUT(serial="SN-001"),
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name="test_capture",
                outcome=Outcome.PASSED,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        observations={
                            "scope": Waveform(t0=0.0, dt=0.001, Y=[1.0, 2.0, 3.0]),
                            "screenshot": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
                        },
                        measurements=[
                            Measurement(name="vout", value=3.3, outcome=Outcome.PASSED),
                        ],
                    ),
                ],
            ),
        ],
    )

    results_root = resolve_data_dir()
    backend = ParquetBackend(data_dir=results_root)
    parquet_path = backend.save_test_run(run)

    notifier = RunStore()
    try:
        notifier.notify_new_run(parquet_path)
    finally:
        notifier.close()

    app = FastAPI()
    app.include_router(create_api_router())
    return TestClient(app), str(run.id)


def _find_ref_uri(rows: list[dict], suffix: str) -> str | None:
    """Walk parquet rows looking for an out_* column whose value ends in *suffix*."""
    for row in rows:
        for key, value in row.items():
            if (
                key.startswith("out_")
                and isinstance(value, str)
                and value.startswith("file://_ref/")
                and value.endswith(suffix)
            ):
                return value
    return None


class TestRefEndpoint:
    def test_waveform_returns_json(self, app_with_run) -> None:
        client, run_id = app_with_run
        rows = client.get(f"/api/runs/{run_id}/measurements").json()["measurements"]
        uri = _find_ref_uri(rows, ".npz")
        assert uri is not None, "no .npz ref in saved rows"

        resp = client.get(f"/api/runs/{run_id}/ref", params={"uri": uri})
        assert resp.status_code == 200
        body = resp.json()
        assert body["Y"] == [1.0, 2.0, 3.0]
        assert body["t0"] == 0.0
        assert body["dt"] == 0.001

    def test_png_bytes_return_image_content_type(self, app_with_run) -> None:
        client, run_id = app_with_run
        rows = client.get(f"/api/runs/{run_id}/measurements").json()["measurements"]
        uri = _find_ref_uri(rows, ".bin")
        assert uri is not None, "no .bin ref in saved rows"

        resp = client.get(f"/api/runs/{run_id}/ref", params={"uri": uri})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    def test_unknown_run_returns_404(self, app_with_run) -> None:
        client, _ = app_with_run
        resp = client.get(
            "/api/runs/nonexistent-run-id/ref",
            params={"uri": "file://_ref/anything.npz"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Run not found"

    def test_missing_ref_file_returns_404(self, app_with_run) -> None:
        client, run_id = app_with_run
        resp = client.get(
            f"/api/runs/{run_id}/ref",
            params={"uri": "file://_ref/does_not_exist.npz"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
