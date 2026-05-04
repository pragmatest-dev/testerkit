"""End-to-end tests pinning the step → run outcome cascade.

Subprocess-style pytest invocations against tiny synthetic test files.
Each test asserts on the resulting parquet so the assertion-pass hook
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

import subprocess
import sys
import textwrap
from pathlib import Path

import duckdb


def _write_test(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))


def _run_pytest(test_file: Path, results_dir: Path) -> subprocess.CompletedProcess:
    """Spawn ``pytest <test_file>`` writing parquet under ``results_dir``."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            f"--results-dir={results_dir}",
            "-v",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _read_step_outcomes(results_dir: Path) -> list[tuple[str, str | None, str | None]]:
    """Return ``[(step_path, step.outcome, run_outcome), ...]`` from parquet."""
    rows = (
        duckdb.connect()
        .execute(
            f"""
        SELECT step_path, outcome, run_outcome
        FROM read_parquet('{results_dir}/runs/*/*_steps.parquet')
        WHERE step_path != ''
        ORDER BY step_path
        """
        )
        .fetchall()
    )
    return [(r[0], r[1], r[2]) for r in rows]


class TestOutcomeCascade:
    """Pin the step → run outcome cascade end-to-end."""

    def test_passing_asserts_land_as_passed(self, tmp_path):
        """A test with passing rewritten asserts → step + run = passed."""
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
        result = _run_pytest(test_file, tmp_path / "results")
        assert result.returncode == 0, (
            f"pytest exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        outcomes = _read_step_outcomes(tmp_path / "results")
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
        result = _run_pytest(test_file, tmp_path / "results")
        assert result.returncode == 0, (
            f"pytest exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        outcomes = _read_step_outcomes(tmp_path / "results")
        assert outcomes == [
            ("test_also_no_judgment", "done", "done"),
            ("test_no_judgment", "done", "done"),
        ], outcomes

    def test_failing_assert_lands_as_failed(self, tmp_path):
        """A test with a failing assert → step + run = failed."""
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
        result = _run_pytest(test_file, tmp_path / "results")
        assert result.returncode != 0, "expected pytest to fail"

        outcomes = _read_step_outcomes(tmp_path / "results")
        # Run cascades to the worst outcome (failed) once any step fails.
        assert outcomes == [
            ("test_fails", "failed", "failed"),
            ("test_passes", "passed", "failed"),
        ], outcomes
