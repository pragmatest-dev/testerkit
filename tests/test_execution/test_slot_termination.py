"""Termination chain — single-process SIGTERM and orchestrator forwarding.

* :class:`TestSingleProcessTermination` — pytest gets SIGTERM →
  step + run land ``terminated`` via ``pytest_keyboard_interrupt``.
  Covers the full handler / cascade / parquet flush chain in one
  process.
* :class:`TestSlotRunnerPropagateTermination` — the orchestrator's
  ``SlotRunner._propagate_termination`` sends SIGTERM only to live
  children, idempotent across calls. Pure logic test, no real
  subprocesses.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from unittest import mock
from uuid import uuid4

from litmus.analysis.runs_query import RunsQuery
from litmus.execution.slot_runner import SlotRunner


def _wait_for_session_runs(session_id: str, expected: int, *, timeout: float = 3.0) -> list:
    """Bounded poll over RunsQuery for a specific session.

    Same canonical daemon every Litmus client uses; filter by the
    test's own ``session_id`` so we ignore everything else in the
    shared store.
    """
    deadline = time.monotonic() + timeout
    q = RunsQuery()
    try:
        while time.monotonic() < deadline:
            runs = q.find_for_session(session_id, include_incomplete=True)
            if len(runs) >= expected:
                return runs
            time.sleep(0.2)
        return q.find_for_session(session_id, include_incomplete=True)
    finally:
        q.close()


class TestSingleProcessTermination:
    """SIGTERM → run lands ``terminated`` (full handler chain)."""

    def test_sigterm_during_test_lands_terminated(self, tmp_path):
        # Subprocess writes to the canonical results_dir (the singleton
        # daemon every Litmus client shares). Test isolation is by the
        # unique ``session_id`` we hand the subprocess via env.
        session_id = str(uuid4())
        marker = tmp_path / "started"
        test_file = tmp_path / "test_slow.py"
        test_file.write_text(
            textwrap.dedent(
                f"""\
                import time
                from pathlib import Path

                def test_slow():
                    Path({str(marker)!r}).touch()
                    time.sleep(30)  # interrupted by SIGTERM
                    assert True
                """
            )
        )

        env = {**os.environ, "_LITMUS_SESSION_ID": session_id}
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-v",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.05)
            assert marker.exists(), "test never reached the sleep marker"

            os.kill(proc.pid, signal.SIGTERM)
            proc.communicate(timeout=20)
        except Exception:
            proc.kill()
            proc.wait(timeout=5)
            raise

        runs = _wait_for_session_runs(session_id, expected=1)
        assert len(runs) == 1, runs
        assert runs[0].outcome == "terminated"
        assert runs[0].ended_at is not None


class TestSlotRunnerPropagateTermination:
    """``SlotRunner._propagate_termination`` forwards SIGTERM correctly.

    Pure-logic test: the live ``_processes`` map is set with mock
    ``Popen`` objects, then ``_propagate_termination`` is invoked and
    we assert which processes it called ``terminate()`` on.
    """

    @staticmethod
    def _make_runner() -> SlotRunner:
        from litmus.data.models import DUT
        from litmus.execution.slots import ResolvedSlot

        slots = {
            "slot_1": ResolvedSlot(slot_id="slot_1", connections={}),
            "slot_2": ResolvedSlot(slot_id="slot_2", connections={}),
        }
        duts = {
            "slot_1": DUT(serial="A"),
            "slot_2": DUT(serial="B"),
        }
        return SlotRunner(slots=slots, duts=duts)

    def test_terminates_only_live_children(self):
        runner = self._make_runner()
        live = mock.Mock(spec=subprocess.Popen)
        live.poll.return_value = None  # still running
        dead = mock.Mock(spec=subprocess.Popen)
        dead.poll.return_value = 0  # already exited
        runner._processes = {"slot_1": live, "slot_2": dead}

        runner._propagate_termination()

        live.terminate.assert_called_once()
        dead.terminate.assert_not_called()

    def test_idempotent_when_called_repeatedly(self):
        """A second call after the first SIGTERM is a no-op for already-exited children.

        After ``terminate()``, the child eventually exits — ``poll()``
        returns a code instead of None. A second
        ``_propagate_termination`` then skips it.
        """
        runner = self._make_runner()
        proc = mock.Mock(spec=subprocess.Popen)
        proc.poll.side_effect = [None, 0]  # alive then dead
        runner._processes = {"slot_1": proc}

        runner._propagate_termination()
        runner._propagate_termination()

        proc.terminate.assert_called_once()

    def test_swallows_terminate_errors(self):
        """``ProcessLookupError`` / ``OSError`` from ``terminate()`` is best-effort."""
        runner = self._make_runner()
        proc = mock.Mock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.terminate.side_effect = ProcessLookupError
        runner._processes = {"slot_1": proc}

        # Must not raise.
        runner._propagate_termination()
