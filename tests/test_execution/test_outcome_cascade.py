"""End-to-end tests pinning the step → run outcome cascade.

Subprocess-style pytest invocations against tiny synthetic test files.
Each test asserts on the resulting step rows so the assertion-pass hook
+ cascade chain is proven end-to-end:

* Passing rewritten asserts must register verdict intent so the step
  lands ``passed`` (not ``done``); the run rolls up the same way.
* Tests without asserts (and without limited measurements) must land
  ``done`` — *not* ``aborted``. The "aborted" close-fallback is only
  for runs that never see ``RunEnded`` (mid-flight kill); a clean
  exit must always cascade through the cleanup chain to a real
  outcome.

Both behaviors are easy to silently break by toggling the wrong
default or losing the ``enable_assertion_pass_hook`` ini setting.
This file is the load-bearing regression net for that.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from uuid import uuid4

from litmus.analysis.runs_query import RunsQuery
from litmus.analysis.steps_query import StepsQuery


def _write_test(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))


def _run_pytest(test_file: Path, *, session_id: str) -> subprocess.CompletedProcess:
    """Spawn ``pytest <test_file>`` writing to the canonical results dir.

    ``session_id`` flows in via ``_LITMUS_SESSION_ID`` so the outer
    test scopes its assertions to exactly this subprocess's run.
    """
    env = {**os.environ, "_LITMUS_SESSION_ID": session_id}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-v",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _read_step_outcomes(
    session_id: str, *, timeout: float = 15.0
) -> list[tuple[str, str | None, str | None]]:
    """Return ``[(step_path, step.outcome, run_outcome), ...]`` for the session.

    Reads through the public Query API (``StepsQuery`` +
    ``RunsQuery``) — same path the operator UI uses. Polls until
    the daemon ingests the subprocess's parquet. Budget is generous
    (15s) because under full-suite load the canonical daemon may
    have queued other tests' notifications ahead of ours.
    """
    deadline = time.monotonic() + timeout
    runs_q = RunsQuery()
    steps_q = StepsQuery()
    try:
        while time.monotonic() < deadline:
            runs = runs_q.list_for_session(session_id, include_incomplete=True)
            if runs:
                break
            time.sleep(0.2)
        else:
            return []

        out: list[tuple[str, str | None, str | None]] = []
        for run in runs:
            assert run.run_id is not None
            for step in steps_q.list_for_run(run.run_id, include_incomplete=True):
                if step.step_path:
                    out.append((step.step_path, step.outcome, run.outcome))
        out.sort()
        return out
    finally:
        runs_q.close()
        steps_q.close()


class TestOutcomeCascade:
    """Pin the step → run outcome cascade end-to-end."""

    def test_passing_asserts_land_as_passed(self, tmp_path):
        """A test with passing rewritten asserts → step + run = passed."""
        session_id = str(uuid4())
        test_file = tmp_path / "test_with_asserts.py"
        _write_test(
            test_file,
            """\
            def test_one():
                assert 1 + 1 == 2

            def test_two():
                assert "abc".upper() == "ABC"
            """,
        )
        result = _run_pytest(test_file, session_id=session_id)
        assert result.returncode == 0, (
            f"pytest exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        outcomes = _read_step_outcomes(session_id)
        assert outcomes == [
            ("test_one", "passed", "passed"),
            ("test_two", "passed", "passed"),
        ], outcomes

    def test_no_asserts_land_as_done(self, tmp_path):
        """A test with no judgment intent → step + run = done.

        Critically NOT ``aborted`` — that fallback is reserved for
        runs that never saw ``RunEnded``. A clean-exit unjudged run
        is a real, recorded outcome.
        """
        session_id = str(uuid4())
        test_file = tmp_path / "test_no_asserts.py"
        _write_test(
            test_file,
            """\
            def test_no_judgment():
                x = 5
                _ = x + 1

            def test_also_no_judgment():
                pass
            """,
        )
        result = _run_pytest(test_file, session_id=session_id)
        assert result.returncode == 0, (
            f"pytest exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        outcomes = _read_step_outcomes(session_id)
        assert outcomes == [
            ("test_also_no_judgment", "done", "done"),
            ("test_no_judgment", "done", "done"),
        ], outcomes

    def test_failing_assert_lands_as_failed(self, tmp_path):
        """A test with a failing assert → step + run = failed."""
        session_id = str(uuid4())
        test_file = tmp_path / "test_fails.py"
        _write_test(
            test_file,
            """\
            def test_passes():
                assert 1 == 1

            def test_fails():
                assert 1 == 2
            """,
        )
        result = _run_pytest(test_file, session_id=session_id)
        assert result.returncode != 0, "expected pytest to fail"

        outcomes = _read_step_outcomes(session_id)
        # Run cascades to the worst outcome (failed) once any step fails.
        assert outcomes == [
            ("test_fails", "failed", "failed"),
            ("test_passes", "passed", "failed"),
        ], outcomes
