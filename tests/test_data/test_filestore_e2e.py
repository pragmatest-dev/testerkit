"""Build item 1e — FileStore end-to-end integration test.

Walks the full happy-path chain for a blob observation:

1. ``Context.observe(name, blob)`` routes the value through
   ``FileStore.put`` immediately (item 3a) and stashes the
   resulting ``file://{session_id}/{filename}`` URI on the active
   vector's ``_observations`` map.
2. Materialization (``ParquetBackend.save_test_run``) writes the
   parquet with the URI verbatim in the ``out_<name>`` column —
   no second copy; ParquetBackend's ref_saver only fires for
   non-URI values (item 1d).
3. ``load_ref`` / ``load_file`` resolve the URI through FileStore
   (no parquet_path needed — FileStore walks date dirs).
4. The original bytes round-trip back.
5. Same chain through the HTTP API: ``/api/runs/{run_id}/ref?uri=...``
   returns the resolved data with the right Content-Type.

Plus failure-mode coverage:

- Missing file (artifact deleted from disk) — load_ref returns the
  URI string unchanged so callers can detect non-resolution.
- Malformed URI — load_ref returns the input verbatim.
- Sidecar metadata is round-trippable via ``FileStore.read_attributes``.

Per CLAUDE.md test conventions: ``resolve_data_dir()`` (canonical)
+ uuid4 session_ids for per-test isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from litmus.api.app import create_api_router
from litmus.data.backends.parquet import ParquetBackend, load_ref
from litmus.data.data_dir import resolve_data_dir
from litmus.data.files import _reset_for_tests, get_filestore
from litmus.data.models import (
    DUT,
    Measurement,
    Outcome,
    TestRun,
    TestStep,
    TestVector,
    Waveform,
)
from litmus.data.run_store import RunStore
from litmus.execution.harness import Context, TestHarness


@pytest.fixture(autouse=True)
def _reset_filestore_singleton() -> None:
    """Each test starts with a fresh ``get_filestore()`` resolution."""
    _reset_for_tests()


@pytest.fixture
def session() -> tuple[Context, UUID]:
    session_id = uuid4()
    harness = TestHarness(session_id=session_id)
    return Context(harness=harness), session_id


# --------------------------------------------------------------------- #
# Step 1: Context.observe → FileStore.put → URI on vector               #
# --------------------------------------------------------------------- #


class TestObserveToFileStore:
    def test_observe_bytes_lands_in_filestore_with_uri(self, session: tuple[Context, UUID]) -> None:
        ctx, session_id = session
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-fake"
        ctx.observe("scope.cap", png)

        uri = ctx._observations["scope.cap"]
        assert uri.startswith(f"file://{session_id}/")
        # FileStore can resolve + read back
        path = get_filestore()._resolve_uri(uri)
        assert path is not None
        assert path.read_bytes() == png

    def test_observe_waveform_lands_as_npz(self, session: tuple[Context, UUID]) -> None:
        ctx, session_id = session
        wf = Waveform(t0=0.0, dt=1e-6, Y=[1.0, 2.0, 3.0], attributes={"units": "V"})
        ctx.observe("scope.waveform", wf)

        uri = ctx._observations["scope.waveform"]
        assert uri.startswith(f"file://{session_id}/")
        assert uri.endswith(".npz")
        # Sidecar carries the right MIME (item 13 convention + item 1c persistence)
        meta = get_filestore().read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/x-numpy-npz"


# --------------------------------------------------------------------- #
# Step 2+3+4: Materialize → URI in parquet → load_ref round-trip        #
# --------------------------------------------------------------------- #


def _make_run_with_observations(session_id, observations: dict) -> TestRun:
    """Build a TestRun whose single vector carries the observations."""
    return TestRun(
        id=uuid4(),
        session_id=session_id,
        started_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 31, 12, 0, 1, tzinfo=UTC),
        dut=DUT(serial="SN-E2E-001"),
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name="test_e2e",
                outcome=Outcome.PASSED,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        observations=observations,
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.31,
                                outcome=Outcome.PASSED,
                            )
                        ],
                    )
                ],
            )
        ],
    )


class TestMaterializeAndLoadBack:
    def test_pre_uri_observation_round_trips_through_parquet(
        self, session: tuple[Context, UUID], tmp_path: Path
    ) -> None:
        """Verb layer wrote URI; materializer carries it; load_ref reads back."""
        ctx, session_id = session
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-roundtrip"
        ctx.observe("screenshot", png)
        uri = ctx._observations["screenshot"]

        # Build TestRun with the URI as the observation value (Context.observe
        # already stamped the URI string; that's what the logger reads).
        run = _make_run_with_observations(session_id, {"screenshot": uri})

        backend = ParquetBackend(data_dir=resolve_data_dir())
        parquet_path = backend.save_test_run(run)

        # Parquet's out_screenshot column carries the URI verbatim —
        # ParquetBackend's ref_saver only fires for non-URI values, so the
        # already-URI'd observation passes through.
        import pyarrow.parquet as pq

        table = pq.read_table(parquet_path)
        rows = [r for r in table.to_pylist() if r.get("record_type") == "measurement"]
        assert rows[0]["out_screenshot"] == uri

        # Load it back via load_ref — gets the original bytes.
        loaded = load_ref(uri, parquet_path=parquet_path)
        assert loaded == png

    def test_post_uri_blob_observation_routes_through_materializer_ref_saver(
        self, tmp_path: Path
    ) -> None:
        """Raw blob (not URI'd by the verb layer) → ref_saver → FileStore.

        Tests the path where a blob arrives at materialization
        without having been claim-checked first (e.g. constructed
        directly into a TestRun).
        """
        session_id = uuid4()
        run = _make_run_with_observations(
            session_id, {"raw_blob": b"\x00\x01\x02not-yet-claim-checked"}
        )

        backend = ParquetBackend(data_dir=resolve_data_dir())
        parquet_path = backend.save_test_run(run)

        import pyarrow.parquet as pq

        table = pq.read_table(parquet_path)
        rows = [r for r in table.to_pylist() if r.get("record_type") == "measurement"]

        # ParquetBackend's ref_saver picked it up and routed through
        # FileStore (item 1d) — URI is in the new shape.
        new_uri = rows[0]["out_raw_blob"]
        assert new_uri.startswith(f"file://{session_id}/")

        # Bytes resolve back through load_ref.
        loaded = load_ref(new_uri, parquet_path=parquet_path)
        assert loaded == b"\x00\x01\x02not-yet-claim-checked"


# --------------------------------------------------------------------- #
# Step 5: HTTP API end-to-end                                            #
# --------------------------------------------------------------------- #


class TestApiRoundTrip:
    def test_observe_bytes_through_api_ref_endpoint(self, session: tuple[Context, UUID]) -> None:
        ctx, session_id = session
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-api-rt"
        ctx.observe("artifact", png)
        uri = ctx._observations["artifact"]

        run = _make_run_with_observations(session_id, {"artifact": uri})
        backend = ParquetBackend(data_dir=resolve_data_dir())
        parquet_path = backend.save_test_run(run)
        notifier = RunStore()
        try:
            notifier.notify_new_run(parquet_path)
        finally:
            notifier.close()

        app = FastAPI()
        app.include_router(create_api_router())
        client = TestClient(app)

        resp = client.get(f"/api/runs/{run.id}/ref", params={"uri": uri})
        assert resp.status_code == 200
        # MIME-sniffed Content-Type for PNG bytes
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == png

    def test_observe_pydantic_model_through_api_ref_endpoint(
        self, session: tuple[Context, UUID]
    ) -> None:
        """Pydantic model lands as .json; API returns the JSON body."""
        ctx, session_id = session

        class Sensor(BaseModel):
            label: str
            reading: float

        ctx.observe("env", Sensor(label="thermistor-a", reading=23.5))
        uri = ctx._observations["env"]

        run = _make_run_with_observations(session_id, {"env": uri})
        backend = ParquetBackend(data_dir=resolve_data_dir())
        parquet_path = backend.save_test_run(run)
        notifier = RunStore()
        try:
            notifier.notify_new_run(parquet_path)
        finally:
            notifier.close()

        app = FastAPI()
        app.include_router(create_api_router())
        client = TestClient(app)

        resp = client.get(f"/api/runs/{run.id}/ref", params={"uri": uri})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"label": "thermistor-a", "reading": 23.5}


# --------------------------------------------------------------------- #
# Failure modes                                                          #
# --------------------------------------------------------------------- #


class TestFailureModes:
    def test_missing_artifact_returns_ref_unchanged(self, session: tuple[Context, UUID]) -> None:
        """When the file on disk is gone, load_ref returns the URI verbatim.

        Lets callers detect non-resolution by comparing input to
        output rather than wrapping in try/except.
        """
        ctx, _session_id = session
        ctx.observe("victim", b"will-be-deleted")
        uri = ctx._observations["victim"]

        # Delete the artifact from disk
        path = get_filestore()._resolve_uri(uri)
        assert path is not None
        path.unlink()

        loaded = load_ref(uri, parquet_path=None)
        assert loaded == uri  # unchanged → caller knows nothing resolved

    def test_malformed_uri_returns_unchanged(self) -> None:
        """A non-URI string flows through ``load_ref`` unchanged."""
        assert load_ref("not://a/real/uri", parquet_path=None) == "not://a/real/uri"
        assert load_ref("just a string", parquet_path=None) == "just a string"
        assert load_ref("", parquet_path=None) == ""

    def test_filestore_uri_pointing_to_unknown_session(self) -> None:
        """URI with a session_id that never wrote anything."""
        bogus = f"file://{uuid4()}/missing.bin"
        # load_ref returns the URI verbatim for unresolvable refs
        assert load_ref(bogus, parquet_path=None) == bogus

    def test_sidecar_metadata_round_trips_for_observed_artifact(
        self, session: tuple[Context, UUID]
    ) -> None:
        """The full chain populates the sidecar (item 1c) so
        ``read_attributes`` returns the real MIME + size."""
        ctx, _session_id = session
        ctx.observe(
            "with_meta",
            b"some bytes here",
        )
        uri = ctx._observations["with_meta"]

        meta = get_filestore().read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/octet-stream"
        assert meta.extension == ".bin"
        assert meta.size_bytes == len(b"some bytes here")
