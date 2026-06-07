"""FastAPI + NiceGUI application."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, ORJSONResponse, Response
from pydantic import BaseModel

from litmus import __version__ as _litmus_version_str
from litmus.api._mime import sniff_mime
from litmus.api.dialogs.models import Dialog, DialogResponse
from litmus.api.models import (
    DialogCreate,
    DialogRespondRequest,
    LaunchRequest,
    RunStatus,
    SaveRequest,
)
from litmus.api.responses import (
    ActiveRunsResponse,
    DialogCreateResponse,
    DialogRespondAck,
    DialogsListResponse,
    GenericObjectResponse,
    InstrumentAssetsResponse,
    InstrumentTypesResponse,
    MatchAllResponse,
    MatchSingleResponse,
    MeasurementsListResponse,
    MetricsResponse,
    ProductRequirementsResponse,
    ProductsListResponse,
    RunLaunchResponse,
    RunsListResponse,
    StationCapabilitiesResponse,
    StationsListResponse,
    StepsListResponse,
    StepsTreeResponse,
)
from litmus.api.schemas import CapabilitySummary, RequirementSummary, RunView, load_run_view
from litmus.data.backends.parquet import ParquetBackend, is_file_reference, load_ref
from litmus.data.models import Waveform
from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.product import Product
from litmus.models.station import StationConfig


def _serialize_ref(result: object) -> Response | dict:
    """Pick a wire format for a materialized ``load_ref`` return value.

    Browser-renderable bytes pass through with a magic-byte-sniffed
    ``Content-Type``; typed values (``Waveform``, Pydantic, ``dict``,
    ``ndarray``, ``pa.Table``) come back as JSON. Anything else is a
    415 — we have nothing useful to give the client.
    """
    if isinstance(result, bytes):
        return Response(content=result, media_type=sniff_mime(result[:64]))
    if isinstance(result, Waveform):
        return result.model_dump()
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        return result

    type_name = type(result).__name__
    if type_name == "ndarray":  # numpy import is heavy; duck-type instead
        return {
            "shape": list(getattr(result, "shape", ())),
            "dtype": str(getattr(result, "dtype", "")),
            "data": result.tolist(),  # type: ignore[attr-defined]
        }
    if type_name == "Table":  # pyarrow.Table — same reasoning
        return {"data": result.to_pylist()}  # type: ignore[attr-defined]

    raise HTTPException(
        status_code=415,
        detail=f"Cannot serialize ref payload of type {type_name!r}",
    )


def _parse_uuid(value: str) -> UUID:
    """Parse a UUID string, raising HTTPException on malformed input.

    400 (malformed) is distinct from 404 (well-formed but unknown id) —
    callers raise 404 themselves after a successful parse.
    """
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc


def _create_dialog_from_request(request: DialogCreate):
    """Factory: build the appropriate Dialog subclass from an API request."""
    from litmus.api.dialogs.models import ChoiceDialog, ConfirmDialog, InputDialog

    if request.type == "choice":
        return ChoiceDialog(
            title=request.title,
            message=request.message,
            run_id=request.run_id,
            step_name=request.step_name,
            timeout_seconds=request.timeout_seconds,
            choices=request.choices or [],
            allow_multiple=request.allow_multiple,
        )
    elif request.type == "input":
        return InputDialog(
            title=request.title,
            message=request.message,
            run_id=request.run_id,
            step_name=request.step_name,
            timeout_seconds=request.timeout_seconds,
            placeholder=request.placeholder,
            default_value=request.default_value,
        )
    else:  # confirm is default
        return ConfirmDialog(
            title=request.title,
            message=request.message,
            run_id=request.run_id,
            step_name=request.step_name,
            timeout_seconds=request.timeout_seconds,
            confirm_label=request.confirm_label,
            cancel_label=request.cancel_label,
        )


def create_api_router() -> APIRouter:
    """Create the JSON API router."""
    router = APIRouter(prefix="/api", tags=["api"])

    from litmus.store import load_project_config

    project = load_project_config()
    data_dir: Path | None = Path(project.data_dir) if project.data_dir else None
    backend = ParquetBackend(data_dir=data_dir)

    # -------------------------------------------------------------------------
    # API docs — Swagger UI / ReDoc / OpenAPI JSON
    #
    # FastAPI's default `/docs`, `/redoc`, `/openapi.json` routes collide
    # with NiceGUI's `@ui.page("/docs")` Diátaxis browser. Mount Swagger
    # UI and ReDoc under the `/api/` prefix so the live API explorer is
    # reachable without shadowing the in-app docs browser.
    # -------------------------------------------------------------------------

    @router.get("/openapi.json", include_in_schema=False)
    def openapi_json(request: Request) -> dict[str, Any]:
        """OpenAPI 3.0 schema for the Litmus HTTP API."""
        return get_openapi(
            title="Litmus HTTP API",
            version=_litmus_version_str,
            description=(
                "JSON API for runs, steps, measurements, sessions, dialogs, "
                "channels, products, stations, instruments, metrics, and the "
                "MCP-parity tool surface."
            ),
            routes=request.app.routes,
        )

    @router.get("/docs", include_in_schema=False, response_class=HTMLResponse)
    def swagger_ui() -> HTMLResponse:
        """Swagger UI live API explorer (mounted under `/api/` to avoid
        colliding with NiceGUI's `/docs` Diátaxis browser).
        """
        return get_swagger_ui_html(
            openapi_url="/api/openapi.json",
            title="Litmus HTTP API — Swagger UI",
        )

    @router.get("/redoc", include_in_schema=False, response_class=HTMLResponse)
    def redoc_ui() -> HTMLResponse:
        """ReDoc rendering of the OpenAPI schema."""
        return get_redoc_html(
            openapi_url="/api/openapi.json",
            title="Litmus HTTP API — ReDoc",
        )

    def _measurements_query():
        from litmus.analysis.measurements_query import MeasurementsQuery

        return MeasurementsQuery(_data_dir=data_dir)

    def _steps_query():
        from litmus.analysis.steps_query import StepsQuery

        return StepsQuery(_data_dir=data_dir)

    # -------------------------------------------------------------------------
    # Runs
    # -------------------------------------------------------------------------

    @router.get("/runs", response_model=RunsListResponse, response_class=ORJSONResponse)
    def list_runs(limit: int = 50):
        """List recent test runs."""
        from litmus.analysis.runs_query import RunsQuery

        q = RunsQuery(_data_dir=data_dir)
        try:
            rows = q.list_recent(limit=limit)
        finally:
            q.close()
        return {"runs": [r.model_dump(exclude={"file_path"}) for r in rows]}

    @router.get("/runs/{run_id}", response_model=RunView, response_class=ORJSONResponse)
    def get_run(run_id: str):
        """Get a specific test run with steps, instruments, and measurements."""
        view = load_run_view(run_id, data_dir=data_dir)
        if view is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return view

    @router.get(
        "/runs/{run_id}/measurements",
        response_model=MeasurementsListResponse,
        response_class=ORJSONResponse,
    )
    def get_measurements(run_id: str):
        """Get measurements for a test run."""
        if backend.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        measurements = backend.get_measurements(run_id)
        return {"measurements": measurements}

    @router.get(
        "/runs/{run_id}/steps",
        response_model=StepsListResponse,
        response_class=ORJSONResponse,
    )
    def get_steps(run_id: str):
        """List steps for a run, ordered by step_index."""
        q = _steps_query()
        try:
            rows = q.list_for_run(run_id)
        finally:
            q.close()
        if not rows:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"steps": [r.model_dump(mode="json") for r in rows]}

    @router.get(
        "/runs/{run_id}/steps/tree",
        response_model=StepsTreeResponse,
        response_class=ORJSONResponse,
    )
    def get_steps_tree(run_id: str):
        """Hierarchical step tree built from ``step_path``."""
        q = _steps_query()
        try:
            tree = q.tree_for_run(run_id)
        finally:
            q.close()
        if not tree:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"tree": [n.model_dump(mode="json") for n in tree]}

    @router.get("/runs/{run_id}/ref")
    def get_ref(run_id: str, uri: str):
        """Materialize a measurement-output ref URI to its underlying data.

        Clients pass the URI from any ``out_*`` column verbatim. Three
        URI shapes are recognized (item 1d dual-path):

        * ``file://{session_id}/{filename}`` — FileStore canonical
          (post-1d)
        * ``file://_ref/{filename}`` — legacy per-parquet sidecar
        * ``channel://scope.ch1?session=...`` — live channel reference

        The endpoint resolves the run's parquet path, calls
        :func:`litmus.data.backends.parquet.load_ref`, then dispatches
        on the materialized type:

        * ``Waveform`` / ``BaseModel`` / ``dict`` / ``pyarrow.Table`` →
          JSON.
        * ``numpy.ndarray`` → JSON ``{shape, dtype, data}``.
        * ``bytes`` → raw response with magic-byte-sniffed Content-Type
          so browsers render images / video / PDF / text inline.
        * Anything else (e.g. arbitrary pickled object) → 415.
        """
        run = backend.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if not run.file_path:
            raise HTTPException(status_code=500, detail="Run has no parquet path")
        parquet_path = Path(run.file_path)

        parent = data_dir if data_dir else Path("results")
        try:
            if uri.startswith("channel://"):
                # Resolve channel refs through the daemon's warm index,
                # not an ephemeral globbing store (req 2).
                from litmus.data.channels.client import channel_query_client

                with channel_query_client(parent / "channels") as client:
                    result = load_ref(uri, parquet_path=parquet_path, channel_store=client)
            else:
                result = load_ref(uri, parquet_path=parquet_path, channel_store=None)
        except Exception as exc:  # noqa: BLE001 — surface load failures uniformly
            raise HTTPException(status_code=502, detail=f"Failed to load ref: {exc}") from exc

        # load_ref returns the URI string unchanged when the underlying
        # file is missing or the scheme isn't a recognized ref.
        if isinstance(result, str) and is_file_reference(uri) and result == uri:
            raise HTTPException(status_code=404, detail=f"Ref payload not found: {uri}")

        return _serialize_ref(result)

    @router.get("/files")
    def get_file(uri: str, request: Request) -> Response:
        """Serve a FileStore artifact directly by ``file://`` URI.

        Companion to :func:`get_ref` for the case where there's no
        materialized run yet — live streams, in-progress captures, any
        FileStore artifact reachable by URI alone.

        ``uri`` must be ``file://{session_id}/{filename}``; the endpoint
        resolves through the FileStore (daemon catalog when warm, else a
        date-dir walk) and serves the bytes with a magic-byte-sniffed
        ``Content-Type``. Honors HTTP ``Range`` (``206 Partial Content``)
        so a consumer can range-read a still-growing stream artifact
        without re-fetching the whole file.
        """
        from litmus.data.files import get_filestore

        if not uri.startswith("file://"):
            raise HTTPException(status_code=400, detail=f"Not a file:// URI: {uri!r}")
        path = get_filestore().resolve_uri(uri)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"Not found: {uri}")

        file_size = path.stat().st_size
        with path.open("rb") as fh:
            content_type = sniff_mime(fh.read(64))

        range_header = request.headers.get("range")
        if not range_header:
            return Response(
                content=path.read_bytes(),
                media_type=content_type,
                headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
            )

        # Parse a single ``bytes=start-end`` range (open-ended ends ok).
        try:
            unit, _, spec = range_header.partition("=")
            start_s, _, end_s = spec.partition("-")
            if unit.strip() != "bytes":
                raise ValueError(f"unsupported range unit: {unit!r}")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Bad Range: {range_header!r} ({exc})"
            ) from exc

        if start < 0 or start >= file_size or start > end:
            raise HTTPException(
                status_code=416,
                detail="Requested Range Not Satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        end = min(end, file_size - 1)
        with path.open("rb") as fh:
            fh.seek(start)
            chunk = fh.read(end - start + 1)
        return Response(
            content=chunk,
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
            },
        )

    @router.post("/runs", response_model=RunLaunchResponse)
    async def start_run(request: LaunchRequest):
        """Start a new test run."""
        from litmus.api.runner import get_runner

        runner = get_runner()
        run_id = await runner.start(request)
        return {"run_id": run_id, "status": "running"}

    @router.get("/runs/{run_id}/status", response_model=RunStatus)
    def get_run_status(run_id: str):
        """Get status of a running test."""
        from litmus.api.runner import get_runner

        runner = get_runner()
        status = runner.get_status(run_id)
        if not status:
            raise HTTPException(status_code=404, detail="Run not found")
        return status

    @router.get("/active", response_model=ActiveRunsResponse)
    def list_active_runs():
        """List currently running tests."""
        from litmus.api.runner import get_runner

        active = get_runner().list_active()
        return {
            "active_runs": [run.model_dump() for run in active],
            "count": len(active),
        }

    # -------------------------------------------------------------------------
    # Dialogs
    # -------------------------------------------------------------------------

    @router.get("/dialogs", response_model=DialogsListResponse)
    def list_dialogs(run_id: str | None = None):
        """List pending dialogs."""
        from litmus.api.dialogs import get_dialog_manager

        manager = get_dialog_manager()
        dialogs = manager.get_pending_dialogs(run_id)
        return {"dialogs": [d.model_dump(mode="json") for d in dialogs]}

    @router.post("/dialogs", response_model=DialogCreateResponse)
    def create_dialog(request: DialogCreate):
        """Create a pending dialog (from test subprocess)."""
        from litmus.api.dialogs import get_dialog_manager

        manager = get_dialog_manager()
        dialog = _create_dialog_from_request(request)
        manager.register_dialog(dialog)
        return {"dialog_id": str(dialog.id), "status": "pending"}

    @router.get("/dialogs/{dialog_id}", response_model=Dialog)
    def get_dialog(dialog_id: str):
        """Get a specific pending dialog."""
        from litmus.api.dialogs import get_dialog_manager

        uuid = _parse_uuid(dialog_id)
        manager = get_dialog_manager()
        for dialog in manager.get_pending_dialogs():
            if dialog.id == uuid:
                return dialog.model_dump(mode="json")
        raise HTTPException(status_code=404, detail="Dialog not found")

    @router.get("/dialogs/{dialog_id}/wait", response_model=DialogResponse)
    async def wait_for_response(dialog_id: str, timeout: float = 300):
        """Long-poll waiting for dialog response.

        Blocks until the dialog is responded to or timeout.
        Used by test subprocesses to wait for operator input.
        """
        from litmus.api.dialogs import DialogResponse, get_dialog_manager

        uuid = _parse_uuid(dialog_id)
        manager = get_dialog_manager()

        # Check if dialog exists
        dialog = next((d for d in manager.get_pending_dialogs() if d.id == uuid), None)

        if not dialog:
            # Maybe already responded
            response = manager.get_response(uuid)
            if response:
                return response.model_dump(mode="json")
            raise HTTPException(status_code=404, detail="Dialog not found")

        # Wait for response with polling
        poll_interval = 0.5
        elapsed = 0.0
        while elapsed < timeout:
            response = manager.get_response(uuid)
            if response:
                return response.model_dump(mode="json")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        return DialogResponse(dialog_id=uuid, timed_out=True).model_dump(mode="json")

    @router.post("/dialogs/{dialog_id}/respond", response_model=DialogRespondAck)
    def respond_to_dialog(dialog_id: str, request: DialogRespondRequest):
        """Respond to a pending dialog."""
        from litmus.api.dialogs import DialogResponse, get_dialog_manager

        uuid = _parse_uuid(dialog_id)
        manager = get_dialog_manager()

        response = DialogResponse(
            dialog_id=uuid,
            confirmed=request.confirmed,
            choice=request.choice,
            choices=request.choices,
            value=request.value,
            cancelled=request.cancelled,
        )

        if manager.respond(uuid, response):
            return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Dialog not found")

    # -------------------------------------------------------------------------
    # Events & Sessions
    # -------------------------------------------------------------------------

    @router.get("/events", response_model=GenericObjectResponse, response_class=ORJSONResponse)
    def list_events(
        session_id: str | None = None,
        type: str | None = None,
        role: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ):
        """Query events from the event store."""
        from litmus.mcp.tools import events_query

        return events_query(
            session_id,
            type,
            role,
            since,
            limit,
            data_dir=data_dir,
        )

    @router.get("/sessions", response_model=GenericObjectResponse, response_class=ORJSONResponse)
    def list_sessions():
        """List known sessions."""
        from litmus.mcp.tools import sessions_query

        return sessions_query(data_dir=data_dir)

    @router.get(
        "/sessions/{session_id}",
        response_model=GenericObjectResponse,
        response_class=ORJSONResponse,
    )
    def get_session(session_id: str):
        """Get events for a specific session."""
        from litmus.mcp.tools import session_detail_query

        result = session_detail_query(session_id, data_dir=data_dir)
        if result is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return result

    # -------------------------------------------------------------------------
    # Channels
    # -------------------------------------------------------------------------

    @router.get("/channels", response_model=GenericObjectResponse, response_class=ORJSONResponse)
    def list_channels():
        """List known channels from the channel registry."""
        from litmus.mcp.tools import channels_list_query

        return channels_list_query(data_dir=data_dir)

    @router.get(
        "/channels/_recent",
        response_model=GenericObjectResponse,
        response_class=ORJSONResponse,
    )
    def list_channels_recent(last_n: int = 50):
        """Channel registry + recent samples per channel.

        Used by the operator UI to render sparkline cells and live-
        updated latest values. ``last_n`` caps the per-channel sample
        count returned (default 50 — enough for a sparkline trace).
        """
        from litmus.mcp.tools import channels_recent_query

        return channels_recent_query(last_n=last_n, data_dir=data_dir)

    @router.get(
        "/channels/{channel_id}",
        response_model=GenericObjectResponse,
        response_class=ORJSONResponse,
    )
    def get_channel_data(
        channel_id: str,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        last_n: int | None = None,
        max_points: int | None = None,
    ):
        """Query channel data."""
        from litmus.mcp.tools import channels_query

        return channels_query(
            channel_id,
            session_id=session_id,
            since=since,
            until=until,
            last_n=last_n,
            max_points=max_points,
            data_dir=data_dir,
        )

    # -------------------------------------------------------------------------
    # Products & Stations
    # -------------------------------------------------------------------------

    @router.get("/products", response_model=ProductsListResponse)
    def list_products():
        """List all available product specifications."""
        from litmus.matching.service import list_products_summary

        products = list_products_summary()
        return {"products": products}

    @router.get("/products/{product_id}", response_model=Product)
    def get_product(product_id: str):
        """Get a product specification by ID."""
        from litmus.store import get_product as store_get_product

        product = store_get_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
        return product.model_dump()

    @router.get(
        "/products/{product_id}/requirements",
        response_model=ProductRequirementsResponse,
    )
    def get_product_requirements(product_id: str):
        """Get required capabilities for a product."""
        from litmus.matching.service import get_required_capabilities
        from litmus.store import get_product as store_get_product

        product = store_get_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
        reqs = get_required_capabilities(product)
        return {
            "product_id": product_id,
            "requirements": [
                RequirementSummary(
                    function=r.function.value,
                    direction=r.direction.value,
                    characteristic_name=r.characteristic_name,
                ).model_dump()
                for r in reqs
            ],
        }

    @router.get("/stations", response_model=StationsListResponse)
    def list_all_stations():
        """List all available test stations."""
        from litmus.store import list_stations

        stations = list_stations()
        return {"stations": [s.model_dump() for s in stations]}

    @router.get("/stations/{station_id}", response_model=StationConfig)
    def get_station(station_id: str):
        """Get a station configuration by ID."""
        from litmus.store import get_station as store_get_station

        config = store_get_station(station_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")
        return config.model_dump()

    @router.get(
        "/stations/{station_id}/capabilities",
        response_model=StationCapabilitiesResponse,
    )
    def get_station_capabilities(station_id: str):
        """Get capabilities provided by a station."""
        from litmus.matching.service import (
            get_station_capabilities as service_get_capabilities,
        )
        from litmus.store import get_station

        config = get_station(station_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")

        capabilities = service_get_capabilities(config)
        return {
            "station_id": station_id,
            "capabilities": [
                CapabilitySummary(
                    function=cap.function.value,
                    direction=cap.direction.value,
                    instrument_type=cap.instrument_type,
                    instrument_name=cap.instrument_name,
                    channel=cap.channel,
                ).model_dump()
                for cap in capabilities
            ],
        }

    @router.get("/match", response_model=MatchSingleResponse | MatchAllResponse)
    def match_capabilities(product_id: str, station_id: str | None = None):
        """Match product requirements to station capabilities.

        If station_id is provided, returns detailed match for that station.
        Otherwise, returns all stations with their compatibility status.
        """
        from litmus.matching.service import (
            find_all_station_matches,
            find_compatible_stations,
        )
        from litmus.store import get_product as store_get_product
        from litmus.store import get_station as store_get_station

        product = store_get_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")

        if station_id:
            # Validate station exists, then find its match result
            config = store_get_station(station_id)
            if not config:
                raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")
            matches = find_compatible_stations(product)
            match = next((m for m in matches if m.station_id == station_id), None)
            return {
                "product_id": product_id,
                "station_id": station_id,
                "compatible": match.compatible if match else False,
            }
        else:
            result = find_all_station_matches(product)
            return {"product_id": product_id, "stations": result}

    # -------------------------------------------------------------------------
    # Instruments & Catalog
    # -------------------------------------------------------------------------

    @router.get("/instruments/types", response_model=InstrumentTypesResponse)
    def list_instrument_types():
        """List distinct instrument ``type`` values present in the catalog."""
        from litmus.store import find_catalog_dirs, load_catalog_from_directory

        types = {
            entry.type
            for cat_dir in find_catalog_dirs()
            for entry in load_catalog_from_directory(cat_dir).values()
            if entry.type
        }
        return {"instrument_types": sorted(types)}

    @router.get("/instruments/catalog/{entry_id}", response_model=InstrumentCatalogEntry)
    def get_catalog_entry(entry_id: str):
        """Get a catalog entry by type or ID."""
        from litmus.store import get_catalog_entry as store_get_catalog_entry

        result = store_get_catalog_entry(entry_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Catalog entry '{entry_id}' not found")
        return result.model_dump()

    @router.get("/instruments/assets", response_model=InstrumentAssetsResponse)
    def list_instrument_assets():
        """List instrument asset files (physical devices you own)."""
        from litmus.store import list_instrument_assets

        assets = list_instrument_assets()
        return {"assets": [a.model_dump() for a in assets], "count": len(assets)}

    @router.get("/instruments/assets/{asset_id}", response_model=InstrumentAssetFile)
    def get_instrument_asset(asset_id: str):
        """Get an instrument asset by ID."""
        from litmus.store import get_instrument_asset

        result = get_instrument_asset(asset_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Instrument asset '{asset_id}' not found")
        return result.model_dump()

    # -------------------------------------------------------------------------
    # Manufacturing-test analytics
    # -------------------------------------------------------------------------

    @router.get("/metrics/summary", response_model=MetricsResponse, response_class=ORJSONResponse)
    def metrics_summary(
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ):
        """Yield summary — DuckDB SQL aggregated from parquet rows at request time."""
        return {
            "data": _measurements_query().yield_summary(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                period=period,
            )
        }

    @router.get("/metrics/pareto", response_model=MetricsResponse, response_class=ORJSONResponse)
    def metrics_pareto(
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        top_n: int = 10,
    ):
        """Top failure modes (DuckDB SQL)."""
        return {
            "data": _measurements_query().pareto(
                product=product, station=station, phase=phase, since=since, until=until, top_n=top_n
            )
        }

    @router.get("/metrics/cpk", response_model=MetricsResponse, response_class=ORJSONResponse)
    def metrics_cpk(
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        min_samples: int = 10,
    ):
        """Process capability (DuckDB SQL)."""
        return {
            "data": _measurements_query().cpk(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                min_samples=min_samples,
            )
        }

    @router.get("/metrics/trend", response_model=MetricsResponse, response_class=ORJSONResponse)
    def metrics_trend(
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ):
        """Yield trend (DuckDB SQL)."""
        return {
            "data": _measurements_query().trend(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                period=period,
            )
        }

    @router.get("/metrics/retest", response_model=MetricsResponse, response_class=ORJSONResponse)
    def metrics_retest(
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ):
        """Retest rates (DuckDB SQL)."""
        return {
            "data": _measurements_query().retest(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                period=period,
            )
        }

    @router.get("/metrics/time-loss", response_model=MetricsResponse, response_class=ORJSONResponse)
    def metrics_time_loss(
        product: str | None = None,
        station: str | None = None,
        phase: str | None = None,
        since: str | None = None,
        until: str | None = None,
        period: str = "day",
    ):
        """Time lost to failures/errors (DuckDB SQL)."""
        return {
            "data": _measurements_query().time_loss(
                product=product,
                station=station,
                phase=phase,
                since=since,
                until=until,
                period=period,
            )
        }

    # -------------------------------------------------------------------------
    # MCP parity endpoints (litmus_discover, litmus_open, litmus_schema, save)
    # -------------------------------------------------------------------------

    @router.get("/discover", response_model=GenericObjectResponse)
    def discover_instruments(protocols: list[str] | None = None):
        """Scan for connected instruments across all protocols.

        HTTP equivalent of the litmus_discover MCP tool.
        """
        from litmus.mcp.tools import discover_tool

        return discover_tool(protocols)

    @router.get("/open", response_model=GenericObjectResponse)
    def open_entity(type: str, id: str, base_url: str = "http://localhost:8000"):
        """Get URL to view/edit an entity in the browser UI.

        HTTP equivalent of the litmus_open MCP tool.
        """
        from litmus.mcp.tools import open_tool

        return open_tool(type, id, base_url)

    @router.get("/schema/{yaml_type}", response_model=GenericObjectResponse)
    def get_yaml_schema(yaml_type: str):
        """Get JSON Schema for a Litmus YAML file type.

        HTTP equivalent of the litmus_schema MCP tool.
        """
        from litmus.mcp.tools import schema_tool

        return schema_tool(yaml_type)

    @router.post("/save/{entity_type}/{entity_id}", response_model=GenericObjectResponse)
    def save_entity(entity_type: str, entity_id: str, request: SaveRequest):
        """Create or update an entity (station, product, sequence, fixture, etc.).

        HTTP equivalent of litmus_project(action='save', ...) MCP tool action.
        Returns validation errors if content does not match the schema.
        """
        from litmus.mcp.tools import litmus_tool

        result = litmus_tool(
            action="save",
            type=entity_type,
            id=entity_id,
            content=request.content,
            project=request.project,
        )
        if isinstance(result, dict) and result.get("success") is False:
            raise HTTPException(
                status_code=422,
                detail=result.get("error") or result.get("errors") or str(result),
            )
        return result

    @router.get("/read", response_model=GenericObjectResponse)
    def read_file(path: str, project: str | None = None):
        """Read a project file or template.

        HTTP equivalent of litmus_project(action='read', ...) MCP tool action.
        Use path='template:test' to get the test file template.
        """
        from litmus.mcp.tools import litmus_tool

        result = litmus_tool(action="read", path=path, project=project)
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @router.get("/enum/{abbrev}", response_model=GenericObjectResponse)
    def lookup_enum(abbrev: str):
        """Resolve a datasheet abbreviation to its MeasurementFunction enum value(s).

        HTTP equivalent of litmus_project(action='lookup_enum', id=abbrev) MCP tool action.
        Example: GET /enum/FRES → resistance_4w
        """
        from litmus.mcp.tools import litmus_tool

        return litmus_tool(action="lookup_enum", id=abbrev)

    @router.get("/enum-reference", response_model=GenericObjectResponse)
    def enum_reference():
        """Get the full abbreviation-to-enum reference table as markdown.

        HTTP equivalent of litmus_project(action='enum_reference') MCP tool action.
        """
        from litmus.mcp.tools import litmus_tool

        return litmus_tool(action="enum_reference")

    return router


def create_app():
    """Create the combined FastAPI + NiceGUI application."""
    from nicegui import app

    # Import UI pages (registers routes)
    import litmus.ui.app  # noqa: F401
    from litmus.api.dialogs import register_as_prompt_handler

    # Add API routes
    api_router = create_api_router()
    app.include_router(api_router)

    # Bridge ``litmus.prompts.ask`` → dialog UI so any test code running
    # in-process routes through the operator UI instead of TTY /
    # auto-confirm. Test subprocesses with ``LITMUS_SERVER_URL`` set
    # install their own bridge in HTTP mode (see pytest plugin).
    register_as_prompt_handler(server_url=None)

    # Diagnostic thread count logger — tracks the slow-leak pattern
    return app
