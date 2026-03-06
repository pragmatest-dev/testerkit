"""FastAPI + NiceGUI application."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException

from litmus.api.models import DialogCreate, DialogRespondRequest, LaunchRequest
from litmus.data.backends.parquet import ParquetBackend


def _parse_uuid(value: str) -> UUID:
    """Parse a UUID string, raising HTTPException on invalid input."""
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid dialog ID")


def _create_dialog_from_request(request: DialogCreate):
    """Factory: build the appropriate Dialog subclass from an API request."""
    from litmus.dialogs.models import ChoiceDialog, ConfirmDialog, InputDialog

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

    from litmus.config.project import load_project_config

    project = load_project_config()
    backend = ParquetBackend(results_dir=project.results_dir)

    # -------------------------------------------------------------------------
    # Runs
    # -------------------------------------------------------------------------

    @router.get("/runs")
    def list_runs(limit: int = 50):
        """List recent test runs."""
        runs = backend.list_runs(limit=limit)
        return {"runs": runs}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        """Get a specific test run."""
        run = backend.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @router.get("/runs/{run_id}/measurements")
    def get_measurements(run_id: str):
        """Get measurements for a test run."""
        measurements = backend.get_measurements(run_id)
        return {"measurements": measurements}

    @router.post("/runs")
    async def start_run(request: LaunchRequest):
        """Start a new test run."""
        from litmus.execution.runner import get_runner

        runner = get_runner()
        run_id = await runner.start(request)
        return {"run_id": run_id, "status": "running"}

    @router.get("/runs/{run_id}/status")
    def get_run_status(run_id: str):
        """Get status of a running test."""
        from litmus.execution.runner import get_runner

        runner = get_runner()
        status = runner.get_status(run_id)
        if not status:
            raise HTTPException(status_code=404, detail="Run not found")
        return status.model_dump()

    @router.get("/active")
    def list_active_runs():
        """List currently running tests."""
        from litmus.execution.runner import get_runner

        runner = get_runner()
        active = []
        for run_id, run_info in runner.runs.items():
            active.append({
                "run_id": run_id,
                "status": run_info.status,
                "progress_pct": run_info.progress_pct,
                "current_step": run_info.current_step,
                "dut_serial": run_info.request.dut_serial,
                "station_id": run_info.request.station_id,
            })
        return {"active_runs": active, "count": len(active)}

    # -------------------------------------------------------------------------
    # Dialogs
    # -------------------------------------------------------------------------

    @router.get("/dialogs")
    def list_dialogs(run_id: str | None = None):
        """List pending dialogs."""
        from litmus.dialogs import get_dialog_manager

        manager = get_dialog_manager()
        dialogs = manager.get_pending_dialogs(run_id)
        return {"dialogs": [d.model_dump(mode="json") for d in dialogs]}

    @router.post("/dialogs")
    def create_dialog(request: DialogCreate):
        """Create a pending dialog (from test subprocess)."""
        from litmus.dialogs import get_dialog_manager

        manager = get_dialog_manager()
        dialog = _create_dialog_from_request(request)
        manager.register_dialog(dialog)
        return {"dialog_id": str(dialog.id), "status": "pending"}

    @router.get("/dialogs/{dialog_id}")
    def get_dialog(dialog_id: str):
        """Get a specific pending dialog."""
        from litmus.dialogs import get_dialog_manager

        uuid = _parse_uuid(dialog_id)
        manager = get_dialog_manager()
        for dialog in manager.get_pending_dialogs():
            if dialog.id == uuid:
                return dialog.model_dump(mode="json")
        raise HTTPException(status_code=404, detail="Dialog not found")

    @router.get("/dialogs/{dialog_id}/wait")
    async def wait_for_response(dialog_id: str, timeout: float = 300):
        """Long-poll waiting for dialog response.

        Blocks until the dialog is responded to or timeout.
        Used by test subprocesses to wait for operator input.
        """
        from litmus.dialogs import get_dialog_manager

        uuid = _parse_uuid(dialog_id)
        manager = get_dialog_manager()

        # Check if dialog exists
        dialog = next(
            (d for d in manager.get_pending_dialogs() if d.id == uuid), None
        )

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

        # Timeout
        return {"dialog_id": str(uuid), "timed_out": True, "confirmed": False}

    @router.post("/dialogs/{dialog_id}/respond")
    def respond_to_dialog(dialog_id: str, request: DialogRespondRequest):
        """Respond to a pending dialog."""
        from litmus.dialogs import DialogResponse, get_dialog_manager

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
    # Products & Stations
    # -------------------------------------------------------------------------

    @router.get("/products")
    def list_products():
        """List all available product specifications."""
        from litmus.matching.service import list_products_summary

        products = list_products_summary()
        return {"products": products}

    @router.get("/products/{product_id}")
    def get_product(product_id: str):
        """Get a product specification by ID."""
        from litmus.store import get_product as store_get_product

        product = store_get_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
        return product.model_dump()

    @router.get("/products/{product_id}/requirements")
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
                {
                    "function": r.function.value,
                    "direction": r.direction.value,
                    "characteristic_name": r.characteristic_name,
                }
                for r in reqs
            ],
        }

    @router.get("/stations")
    def list_all_stations():
        """List all available test stations."""
        from litmus.store import list_stations

        stations = list_stations()
        return {"stations": [s.model_dump() for s in stations]}

    @router.get("/stations/{station_id}")
    def get_station(station_id: str):
        """Get a station configuration by ID."""
        from litmus.store import get_station as store_get_station

        config = store_get_station(station_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")
        return config.model_dump()

    @router.get("/stations/{station_id}/capabilities")
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
                {
                    "function": cap.function.value,
                    "direction": cap.direction.value,
                    "instrument_type": cap.instrument_type,
                    "instrument_name": cap.instrument_name,
                    "channel": cap.channel,
                }
                for cap in capabilities
            ],
        }

    @router.get("/match")
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
                raise HTTPException(
                    status_code=404, detail=f"Station '{station_id}' not found"
                )
            matches = find_compatible_stations(product)
            match = next(
                (m for m in matches if m.station_id == station_id), None
            )
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

    @router.get("/instruments/catalog")
    def list_catalog_entries():
        """List available catalog entries (instrument models and capabilities)."""
        from litmus.store import find_catalog_dirs, load_catalog_from_directory

        seen: set[str] = set()
        types: list[str] = []
        for cat_dir in find_catalog_dirs():
            for entry_id, entry in load_catalog_from_directory(cat_dir).items():
                if entry.type not in seen:
                    seen.add(entry.type)
                    types.append(entry.type)
        return {"instrument_types": sorted(types)}

    @router.get("/instruments/catalog/{entry_id}")
    def get_catalog_entry(entry_id: str):
        """Get a catalog entry by type or ID."""
        from litmus.store import get_catalog_entry as store_get_catalog_entry

        result = store_get_catalog_entry(entry_id)
        if not result:
            raise HTTPException(
                status_code=404, detail=f"Catalog entry '{entry_id}' not found"
            )
        return result.model_dump()

    @router.get("/instruments/assets")
    def list_instrument_assets():
        """List instrument asset files (physical devices you own)."""
        from litmus.store import list_instrument_assets

        assets = list_instrument_assets()
        return {"assets": [a.model_dump() for a in assets], "count": len(assets)}

    @router.get("/instruments/assets/{asset_id}")
    def get_instrument_asset(asset_id: str):
        """Get an instrument asset by ID."""
        from litmus.store import get_instrument_asset

        result = get_instrument_asset(asset_id)
        if not result:
            raise HTTPException(
                status_code=404, detail=f"Instrument asset '{asset_id}' not found"
            )
        return result.model_dump()

    # -------------------------------------------------------------------------
    # Sequences
    # -------------------------------------------------------------------------

    @router.get("/sequences")
    def list_all_sequences():
        """List available test sequences."""
        from litmus.store import list_sequences

        sequences = list_sequences()
        return {"sequences": [s.model_dump() for s in sequences]}

    return router


def create_app():
    """Create the combined FastAPI + NiceGUI application."""
    from nicegui import app

    # Import UI pages (registers routes)
    import litmus.ui.app  # noqa: F401

    # Add API routes
    api_router = create_api_router()
    app.include_router(api_router)

    return app
