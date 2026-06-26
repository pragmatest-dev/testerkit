"""Async test runner with progress streaming."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from litmus.api.models import ActiveRun, LaunchRequest, RunStatus
from litmus.data.data_dir import resolve_data_dir

logger = logging.getLogger(__name__)


@dataclass
class RunInfo:
    """Information about a running or completed test run."""

    run_id: str
    request: LaunchRequest
    process: asyncio.subprocess.Process | None = None
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    progress_pct: int = 0
    current_step: str | None = None
    output_lines: list[str] = field(default_factory=list)
    returncode: int | None = None


class TestRunner:
    """Manages test run execution via subprocess."""

    __test__ = False  # Prevent pytest collection

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir()
        self.runs: dict[str, RunInfo] = {}

    async def start(self, request: LaunchRequest) -> str:
        """Start a test run and return run ID."""
        run_id = str(uuid.uuid4())
        run_info = RunInfo(run_id=run_id, request=request, status="pending")
        self.runs[run_id] = run_info

        # Start test execution in background
        asyncio.create_task(self._run_tests(run_info))

        return run_id

    async def _run_tests(self, run_info: RunInfo) -> None:
        """Execute pytest in subprocess."""
        import os

        run_info.status = "running"
        req = run_info.request

        # pytest discovers tests in the requested path / node-id list.
        test_targets = [req.test_path]

        # Use uv run to ensure pytest (dev dependency) is available
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            *test_targets,
            f"--uut-serial={req.uut_serial}",
            f"--station={req.station_id}",
            f"--data-dir={self.data_dir}",
            "-v",
            "--tb=short",
        ]

        if req.operator:
            cmd.append(f"--operator={req.operator}")
        if req.part_id:
            cmd.append(f"--part={req.part_id}")
        if req.test_profile:
            cmd.append(f"--test-profile={req.test_profile}")

        # Set up environment for subprocess
        env = os.environ.copy()
        # Pass server URL so dialogs can communicate back
        env["LITMUS_SERVER_URL"] = os.environ.get("LITMUS_SERVER_URL", "http://localhost:8000")
        # Pass run ID so dialogs are linked to this run
        env["_LITMUS_RUN_ID"] = run_info.run_id
        # Pass session ID for multi-slot coordination
        env["_LITMUS_SESSION_ID"] = run_info.run_id
        # Enable mock instruments if requested
        if req.mock_instruments:
            env["LITMUS_MOCK_INSTRUMENTS"] = "1"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        run_info.process = proc

        # Read output lines
        if proc.stdout:
            async for line in proc.stdout:
                decoded = line.decode().rstrip()
                run_info.output_lines.append(decoded)
                # Parse pytest output for progress hints
                self._parse_output_line(run_info, decoded)

        await proc.wait()
        run_info.returncode = proc.returncode
        run_info.status = "completed" if proc.returncode == 0 else "failed"
        run_info.progress_pct = 100

    def _parse_output_line(self, run_info: RunInfo, line: str) -> None:
        """Parse pytest output for progress information."""
        # Simple heuristics for progress
        if "PASSED" in line or "FAILED" in line or "ERROR" in line:
            # Extract test name
            parts = line.split("::")
            if len(parts) >= 2:
                run_info.current_step = parts[-1].split()[0]
            # Increment progress (rough estimate)
            run_info.progress_pct = min(run_info.progress_pct + 10, 95)
        elif "collecting" in line.lower():
            run_info.current_step = "Collecting tests..."
        elif "=" in line and ("passed" in line or "failed" in line):
            # Final summary line
            run_info.progress_pct = 100

    def get_status(self, run_id: str) -> RunStatus | None:
        """Get current status of a run."""
        run_info = self.runs.get(run_id)
        if not run_info:
            return None
        return RunStatus(
            run_id=run_info.run_id,
            status=run_info.status,
            progress_pct=run_info.progress_pct,
            current_step=run_info.current_step,
        )

    def list_active(self) -> list[ActiveRun]:
        """Return a typed snapshot of every tracked run.

        Currently returns all runs (including completed ones) since the
        endpoint that consumes this displays history alongside live runs.
        """
        return [
            ActiveRun(
                run_id=run_id,
                status=info.status,
                progress_pct=info.progress_pct,
                current_step=info.current_step,
                uut_serial=info.request.uut_serial,
                station_id=info.request.station_id,
            )
            for run_id, info in self.runs.items()
        ]

    async def stream(self, run_id: str) -> AsyncIterator[dict]:
        """Stream progress events for a run."""
        run_info = self.runs.get(run_id)
        if not run_info:
            yield {"type": "error", "message": "Run not found"}
            return

        # Track what we've already sent
        sent_lines = 0

        while run_info.status in ("pending", "running"):
            # Send new output lines
            new_lines = run_info.output_lines[sent_lines:]
            for line in new_lines:
                yield {"type": "output", "data": line}
            sent_lines = len(run_info.output_lines)

            # Send status update
            yield {
                "type": "progress",
                "status": run_info.status,
                "progress_pct": run_info.progress_pct,
                "current_step": run_info.current_step,
            }

            await asyncio.sleep(0.2)

        # Send any remaining output
        new_lines = run_info.output_lines[sent_lines:]
        for line in new_lines:
            yield {"type": "output", "data": line}

        # Send completion event
        yield {
            "type": "complete",
            "run_id": run_id,
            "returncode": run_info.returncode,
            "status": run_info.status,
        }


# Global runner instance
_runner: TestRunner | None = None


def get_runner() -> TestRunner:
    """Get or create the global test runner.

    On first access we resolve the project's data dir (``litmus.yaml``
    ``data_dir``, else the global default) so the subprocess writes to
    the same parquet tree that ``ParquetBackend`` reads from. Without
    this, runs launched via the API would land in ``./results`` while
    the read side looks elsewhere, and the new run would be invisible in
    run listings.
    """
    global _runner
    if _runner is None:
        _runner = TestRunner(data_dir=resolve_data_dir())
    return _runner
