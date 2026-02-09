"""FastAPI + NiceGUI application."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from litmus.api.models import LaunchRequest
from litmus.data.backends.parquet import ParquetBackend


class DialogCreate(BaseModel):
    """Request body for creating a dialog."""

    type: str = "confirm"
    title: str
    message: str
    run_id: str | None = None
    step_name: str | None = None
    timeout_seconds: float | None = None
    # For choice dialogs
    choices: list[str] | None = None
    allow_multiple: bool = False
    # For input dialogs
    placeholder: str = ""
    default_value: str = ""
    # For confirm dialogs
    confirm_label: str = "Confirm"
    cancel_label: str = "Cancel"


class DialogRespondRequest(BaseModel):
    """Request body for responding to a dialog."""

    confirmed: bool = False
    choice: int | None = None
    choices: list[int] | None = None
    value: str | None = None
    cancelled: bool = False


def create_api_router() -> APIRouter:
    """Create the JSON API router."""
    router = APIRouter(prefix="/api", tags=["api"])

    @router.get("/runs")
    def list_runs(limit: int = 50):
        """List recent test runs."""
        backend = ParquetBackend(results_dir="results")
        runs = backend.list_runs(limit=limit)
        return {"runs": runs}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        """Get a specific test run."""
        backend = ParquetBackend(results_dir="results")
        run = backend.get_run(run_id)
        if not run:
            return {"error": "Run not found"}, 404
        return run

    @router.get("/runs/{run_id}/measurements")
    def get_measurements(run_id: str):
        """Get measurements for a test run."""
        backend = ParquetBackend(results_dir="results")
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
            return {"error": "Run not found"}, 404
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

    # Dialog endpoints
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
        from litmus.dialogs.models import (
            ChoiceDialog,
            ConfirmDialog,
            InputDialog,
        )

        manager = get_dialog_manager()

        # Create appropriate dialog type
        if request.type == "choice":
            dialog = ChoiceDialog(
                title=request.title,
                message=request.message,
                run_id=request.run_id,
                step_name=request.step_name,
                timeout_seconds=request.timeout_seconds,
                choices=request.choices or [],
                allow_multiple=request.allow_multiple,
            )
        elif request.type == "input":
            dialog = InputDialog(
                title=request.title,
                message=request.message,
                run_id=request.run_id,
                step_name=request.step_name,
                timeout_seconds=request.timeout_seconds,
                placeholder=request.placeholder,
                default_value=request.default_value,
            )
        else:  # confirm is default
            dialog = ConfirmDialog(
                title=request.title,
                message=request.message,
                run_id=request.run_id,
                step_name=request.step_name,
                timeout_seconds=request.timeout_seconds,
                confirm_label=request.confirm_label,
                cancel_label=request.cancel_label,
            )

        # Register the dialog (makes it pending)
        manager.register_dialog(dialog)

        return {"dialog_id": str(dialog.id), "status": "pending"}

    @router.get("/dialogs/{dialog_id}")
    def get_dialog(dialog_id: str):
        """Get a specific pending dialog."""
        from uuid import UUID

        from litmus.dialogs import get_dialog_manager

        manager = get_dialog_manager()
        try:
            uuid = UUID(dialog_id)
        except ValueError:
            return {"error": "Invalid dialog ID"}, 400

        for dialog in manager.get_pending_dialogs():
            if dialog.id == uuid:
                return dialog.model_dump(mode="json")
        return {"error": "Dialog not found"}, 404

    @router.get("/dialogs/{dialog_id}/wait")
    async def wait_for_response(dialog_id: str, timeout: float = 300):
        """Long-poll waiting for dialog response.

        Blocks until the dialog is responded to or timeout.
        Used by test subprocesses to wait for operator input.
        """
        from uuid import UUID

        from litmus.dialogs import get_dialog_manager

        manager = get_dialog_manager()
        try:
            uuid = UUID(dialog_id)
        except ValueError:
            return {"error": "Invalid dialog ID"}, 400

        # Check if dialog exists
        dialog = None
        for d in manager.get_pending_dialogs():
            if d.id == uuid:
                dialog = d
                break

        if not dialog:
            # Maybe already responded - check responses
            response = manager.get_response(uuid)
            if response:
                return response.model_dump(mode="json")
            return {"error": "Dialog not found"}, 404

        # Wait for response with polling
        poll_interval = 0.5
        elapsed = 0.0
        while elapsed < timeout:
            response = manager.get_response(uuid)
            if response:
                return response.model_dump(mode="json")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout - return timeout response
        return {"dialog_id": str(uuid), "timed_out": True, "confirmed": False}

    @router.post("/dialogs/{dialog_id}/respond")
    def respond_to_dialog(dialog_id: str, request: DialogRespondRequest):
        """Respond to a pending dialog."""
        from uuid import UUID

        from litmus.dialogs import DialogResponse, get_dialog_manager

        manager = get_dialog_manager()
        try:
            uuid = UUID(dialog_id)
        except ValueError:
            return {"error": "Invalid dialog ID"}, 400

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
        return {"error": "Dialog not found"}, 404

    # -------------------------------------------------------------------------
    # Matching API endpoints
    # -------------------------------------------------------------------------

    @router.get("/products")
    def list_products():
        """List all available product specifications."""
        from litmus.matching.service import list_products as service_list_products

        products = service_list_products()
        return {"products": products}

    @router.get("/products/{product_id}")
    def get_product(product_id: str):
        """Get a product specification by ID."""
        from litmus.mcp.tools import get_product_spec_tool

        result = get_product_spec_tool(product_id)
        if "error" in result:
            return {"error": result["error"]}, 404
        return result

    @router.get("/products/{product_id}/requirements")
    def get_product_requirements(product_id: str):
        """Get required capabilities for a product."""
        from litmus.mcp.tools import derive_required_capabilities_tool

        capabilities = derive_required_capabilities_tool(product_id)
        if capabilities and "error" in capabilities[0]:
            return {"error": capabilities[0]["error"]}, 404
        return {"product_id": product_id, "requirements": capabilities}

    @router.get("/stations")
    def list_all_stations():
        """List all available test stations."""
        from litmus.matching.service import list_stations as service_list_stations

        stations = service_list_stations()
        return {"stations": stations}

    @router.get("/stations/{station_id}")
    def get_station(station_id: str):
        """Get a station configuration by ID."""
        from litmus.mcp.tools import get_station_config_tool

        result = get_station_config_tool(station_id)
        if "error" in result:
            return {"error": result["error"]}, 404
        return result

    @router.get("/stations/{station_id}/capabilities")
    def get_station_capabilities(station_id: str):
        """Get capabilities provided by a station."""
        from litmus.matching.service import (
            get_station_capabilities as service_get_capabilities,
        )
        from litmus.matching.service import (
            load_station_config,
        )

        config = load_station_config(station_id)
        if not config:
            return {"error": f"Station '{station_id}' not found"}, 404

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
        from litmus.mcp.tools import (
            check_station_compatibility_tool,
            find_compatible_stations_tool,
        )

        if station_id:
            result = check_station_compatibility_tool(product_id, station_id)
            if "error" in result:
                return {"error": result["error"]}, 404
            return result
        else:
            stations = find_compatible_stations_tool(product_id)
            if stations and "error" in stations[0]:
                return {"error": stations[0]["error"]}, 404
            return {"product_id": product_id, "stations": stations}

    @router.get("/instruments")
    def list_instruments():
        """List available instrument types in the library."""
        from litmus.matching.service import list_instrument_types

        types = list_instrument_types()
        return {"instrument_types": types}

    @router.get("/instruments/{instrument_type}")
    def get_instrument(instrument_type: str):
        """Get an instrument definition from the library."""
        from litmus.mcp.tools import get_instrument_library_tool

        result = get_instrument_library_tool(instrument_type)
        if "error" in result:
            return {"error": result["error"]}, 404
        return result

    @router.get("/sequences")
    def list_all_sequences():
        """List available test sequences."""
        from litmus.mcp.tools import list_sequences_tool

        sequences = list_sequences_tool()
        return {"sequences": sequences}

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
