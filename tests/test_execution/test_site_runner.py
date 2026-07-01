"""Tests for subprocess-based parallel site execution."""

import sys
from uuid import uuid4

import pytest

from litmus.data.models import UUT
from litmus.execution.site_runner import SiteRunner
from litmus.execution.sites import ResolvedSite


def _make_sites() -> list[ResolvedSite]:
    """Create two resolved sites for testing."""
    return [
        ResolvedSite(site_index=0),
        ResolvedSite(site_index=1),
    ]


def _make_uuts() -> dict[int, UUT]:
    return {
        0: UUT(serial="SN001"),
        1: UUT(serial="SN002"),
    }


class TestSiteRunnerExecution:
    """SiteRunner spawns subprocesses with correct env vars."""

    def test_runs_both_sites(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        cmd = [sys.executable, "-c", "import os; print(os.environ.get('_LITMUS_SITE_INDEX'))"]
        results = runner.run(cmd, sync=False)

        assert len(results) == 2
        assert results[0].outcome == "passed"
        assert results[1].outcome == "passed"

    def test_each_site_gets_correct_env_vars(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        script = (
            "import os, json; print(json.dumps({"
            "'site': os.environ.get('_LITMUS_SITE_INDEX'),"
            "'serial': os.environ.get('LITMUS_UUT_SERIAL'),"
            "'count': os.environ.get('_LITMUS_SITE_COUNT'),"
            "'session': os.environ.get('_LITMUS_SESSION_ID')"
            "}))"
        )
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        import json

        for site_index in (0, 1):
            result = results[site_index]
            assert result.outcome == "passed"
            assert len(result.output_lines) >= 1
            data = json.loads(result.output_lines[0])
            assert data["site"] == str(site_index)
            assert data["serial"] == uuts[site_index].serial
            assert data["count"] == "2"
            assert data["session"] == str(runner.session_id)

    def test_shared_session_id(self):
        sites = _make_sites()
        uuts = _make_uuts()
        session_id = uuid4()
        runner = SiteRunner(sites, uuts, session_id=session_id)

        script = "import os; print(os.environ.get('_LITMUS_SESSION_ID'))"
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        for site_index in (0, 1):
            assert results[site_index].output_lines[0] == str(session_id)

    def test_pass_outcome_on_success(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        cmd = [sys.executable, "-c", "pass"]
        results = runner.run(cmd, sync=False)

        assert results[0].outcome == "passed"
        assert results[0].returncode == 0

    def test_fail_outcome_on_error(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        # site 1 exits with error
        script = "import os, sys; sys.exit(1 if os.environ.get('_LITMUS_SITE_INDEX') == '1' else 0)"
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        assert results[0].outcome == "passed"
        assert results[1].outcome == "failed"
        assert results[1].returncode == 1

    def test_captures_stdout(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        cmd = [sys.executable, "-c", "print('hello from site')"]
        results = runner.run(cmd, sync=False)

        assert "hello from site" in results[0].output_lines

    def test_fixture_site_json_in_env(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        script = (
            "import os, json; "
            "data = json.loads(os.environ['LITMUS_FIXTURE_SITE']); "
            "print(data['site_index'])"
        )
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        assert results[0].output_lines[0] == "0"
        assert results[1].output_lines[0] == "1"


class TestSiteRunnerValidation:
    """Input validation."""

    def test_empty_sites_raises(self):
        with pytest.raises(ValueError, match="At least one site"):
            SiteRunner([], {})

    def test_missing_uut_raises(self):
        sites = _make_sites()
        with pytest.raises(ValueError, match="Missing UUT identity"):
            SiteRunner(
                sites,
                {0: UUT(serial="SN001")},  # site 1 missing
            )

    def test_extra_env_vars_passed(self):
        sites = _make_sites()
        uuts = _make_uuts()
        runner = SiteRunner(sites, uuts)

        script = "import os; print(os.environ.get('MY_CUSTOM_VAR', 'not_set'))"
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False, env={"MY_CUSTOM_VAR": "hello"})

        assert results[0].output_lines[0] == "hello"
