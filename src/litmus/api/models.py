"""API request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class LaunchRequest(BaseModel):
    """Request to launch a test run."""

    part_id: str | None = None  # Part being tested
    uut_serial: str
    station_id: str
    test_path: str = "tests"  # pytest target — directory or node-id list
    test_profile: str | None = None  # Profile name from litmus.yaml; passed as --test-profile
    operator: str | None = None
    mock_instruments: bool = False  # Use mock instruments instead of real hardware


class RunStatus(BaseModel):
    """Status of a test run."""

    run_id: str
    status: Literal["pending", "running", "completed", "failed"]
    progress_pct: int = 0
    current_step: str | None = None


class ActiveRun(BaseModel):
    """Public summary of one currently-tracked run.

    Returned by ``TestRunner.list_active()``; consumed by the
    ``/api/active`` endpoint and the live UI.
    """

    run_id: str
    status: Literal["pending", "running", "completed", "failed"]
    progress_pct: int = 0
    current_step: str | None = None
    uut_serial: str
    station_id: str


class DialogCreate(BaseModel):
    """Request body for creating a dialog."""

    type: Literal["confirm", "choice", "input"] = "confirm"
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


class SaveRequest(BaseModel):
    """Request body for saving an entity via the unified save endpoint."""

    content: dict[str, Any]
    project: str | None = None
