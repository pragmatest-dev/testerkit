"""API request/response models."""

from pydantic import BaseModel


class LaunchRequest(BaseModel):
    """Request to launch a test run."""

    dut_serial: str
    station_id: str
    sequence_id: str | None = None  # Test sequence to run (from sequences/*.yaml)
    test_path: str = "tests"  # Fallback if no sequence specified
    operator: str | None = None


class RunStatus(BaseModel):
    """Status of a test run."""

    run_id: str
    status: str  # pending, running, completed, failed
    progress_pct: int = 0
    current_step: str | None = None
