"""Async test runner with progress streaming."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from litmus.api.models import LaunchRequest, RunStatus


@dataclass
class RunInfo:
    """Information about a running or completed test run."""

    run_id: str
    request: LaunchRequest
    process: asyncio.subprocess.Process | None = None
    status: str = "pending"  # pending, running, completed, failed
    progress_pct: int = 0
    current_step: str | None = None
    output_lines: list[str] = field(default_factory=list)
    returncode: int | None = None


class TestRunner:
    """Manages test run execution via subprocess."""

    def __init__(self, results_dir: Path | str = "results"):
        self.results_dir = Path(results_dir)
        self.runs: dict[str, RunInfo] = {}
        self._sequences: dict[str, dict] | None = None

    def _load_sequence(self, sequence_id: str) -> dict | None:
        """Load test sequence configuration by ID."""
        if self._sequences is None:
            self._sequences = self._discover_sequences()
        return self._sequences.get(sequence_id)

    def _expand_sequence(self, sequence_id: str, visited: set[str] | None = None) -> list[str]:
        """Recursively expand sequence to list of pytest node IDs.

        Args:
            sequence_id: ID of sequence to expand
            visited: Set of already-visited sequence IDs (cycle detection)

        Returns:
            List of pytest node IDs in execution order
        """
        if visited is None:
            visited = set()

        # Cycle detection
        if sequence_id in visited:
            raise ValueError(f"Circular sequence reference detected: {sequence_id}")
        visited.add(sequence_id)

        seq = self._load_sequence(sequence_id)
        if not seq:
            return []

        test_nodes = []
        for step in seq.get("steps", []):
            if step.get("test"):
                test_nodes.append(step["test"])
            elif step.get("sequence"):
                # Recursively expand nested sequence
                test_nodes.extend(self._expand_sequence(step["sequence"], visited))

        return test_nodes

    def _discover_sequences(self) -> dict[str, dict]:
        """Discover test sequences from YAML configuration files."""
        import yaml

        sequences = {}
        search_paths = [
            Path.cwd() / "sequences",
            Path.cwd() / "tests" / "sequences",
        ]

        for seq_dir in search_paths:
            if not seq_dir.exists():
                continue
            for yaml_path in seq_dir.glob("*.yaml"):
                with open(yaml_path) as f:
                    data = yaml.safe_load(f)
                    if data and "sequence" in data:
                        seq = data["sequence"]
                        seq_id = seq.get("id", yaml_path.stem)
                        sequences[seq_id] = seq

        return sequences

    def get_available_sequences(self) -> list[dict]:
        """Get list of available test sequences for UI."""
        if self._sequences is None:
            self._sequences = self._discover_sequences()
        return [
            {
                "id": s.get("id"),
                "name": s.get("name") or s.get("id"),
                "description": s.get("description", ""),
                "product_family": s.get("product_family", ""),
                "test_phase": s.get("test_phase", ""),
            }
            for s in self._sequences.values()
        ]

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
        run_info.status = "running"
        req = run_info.request

        # Determine test targets and extra args
        test_targets: list[str] = []
        extra_args: list[str] = []

        if req.sequence_id:
            # Mode 1: Sequence-driven - expand steps to pytest node IDs
            test_targets = self._expand_sequence(req.sequence_id)
            seq = self._load_sequence(req.sequence_id)
            if seq:
                extra_args.extend(seq.get("pytest_args", []))
        else:
            # Mode 2: Discovery fallback - pytest discovers tests in path
            test_targets = [req.test_path]

        # Use uv run to ensure pytest (dev dependency) is available
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            *test_targets,
            f"--dut-serial={req.dut_serial}",
            f"--station={req.station_id}",
            f"--results-dir={self.results_dir}",
            "-v",
            "--tb=short",
        ]

        # Add extra pytest args from sequence
        cmd.extend(extra_args)

        if req.operator:
            cmd.append(f"--operator={req.operator}")

        # Set up environment for subprocess
        import os

        env = os.environ.copy()
        # Pass server URL so dialogs can communicate back
        env["LITMUS_SERVER_URL"] = os.environ.get("LITMUS_SERVER_URL", "http://localhost:8000")
        # Pass run ID so dialogs are linked to this run
        env["LITMUS_RUN_ID"] = run_info.run_id

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
    """Get or create the global test runner."""
    global _runner
    if _runner is None:
        _runner = TestRunner()
    return _runner
