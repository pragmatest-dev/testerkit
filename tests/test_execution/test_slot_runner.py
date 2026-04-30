"""Tests for subprocess-based parallel slot execution."""

import sys
from uuid import uuid4

import pytest

from litmus.data.models import DUT
from litmus.execution.slot_runner import SlotRunner
from litmus.execution.slots import ResolvedSlot, resolve_fixture_slots
from litmus.models.test_config import FixtureConfig, FixtureConnection, FixtureSlot


def _make_slots() -> dict[str, ResolvedSlot]:
    """Create two resolved slots for testing."""
    fc = FixtureConfig(
        id="test_fixture",
        slots={
            "slot_1": FixtureSlot(
                connections={
                    "vout": FixtureConnection(
                        name="vout",
                        instrument="dmm",
                        instrument_channel="1",
                    )
                },
            ),
            "slot_2": FixtureSlot(
                connections={
                    "vout": FixtureConnection(
                        name="vout",
                        instrument="dmm",
                        instrument_channel="2",
                    )
                },
            ),
        },
    )
    return resolve_fixture_slots(fc)


def _make_duts() -> dict[str, DUT]:
    return {
        "slot_1": DUT(serial="SN001"),
        "slot_2": DUT(serial="SN002"),
    }


class TestSlotRunnerExecution:
    """SlotRunner spawns subprocesses with correct env vars."""

    def test_runs_both_slots(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        # Run a simple command that succeeds
        cmd = [sys.executable, "-c", "import os; print(os.environ.get('LITMUS_SLOT_ID'))"]
        results = runner.run(cmd, sync=False)

        assert len(results) == 2
        assert results["slot_1"].outcome == "passed"
        assert results["slot_2"].outcome == "passed"

    def test_each_slot_gets_correct_env_vars(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        # Print env vars so we can verify
        script = (
            "import os, json; print(json.dumps({"
            "'slot': os.environ.get('LITMUS_SLOT_ID'),"
            "'serial': os.environ.get('LITMUS_DUT_SERIAL'),"
            "'count': os.environ.get('LITMUS_SLOT_COUNT'),"
            "'session': os.environ.get('LITMUS_SESSION_ID')"
            "}))"
        )
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        import json

        for slot_id in ("slot_1", "slot_2"):
            result = results[slot_id]
            assert result.outcome == "passed"
            assert len(result.output_lines) >= 1
            data = json.loads(result.output_lines[0])
            assert data["slot"] == slot_id
            assert data["serial"] == duts[slot_id].serial
            assert data["count"] == "2"
            assert data["session"] == str(runner.session_id)

    def test_shared_session_id(self):
        slots = _make_slots()
        duts = _make_duts()
        session_id = uuid4()
        runner = SlotRunner(slots, duts, session_id=session_id)

        script = "import os; print(os.environ.get('LITMUS_SESSION_ID'))"
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        for slot_id in ("slot_1", "slot_2"):
            assert results[slot_id].output_lines[0] == str(session_id)

    def test_pass_outcome_on_success(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        cmd = [sys.executable, "-c", "pass"]
        results = runner.run(cmd, sync=False)

        assert results["slot_1"].outcome == "passed"
        assert results["slot_1"].returncode == 0

    def test_fail_outcome_on_error(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        # slot_2 exits with error
        script = (
            "import os, sys; sys.exit(1 if os.environ.get('LITMUS_SLOT_ID') == 'slot_2' else 0)"
        )
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        assert results["slot_1"].outcome == "passed"
        assert results["slot_2"].outcome == "failed"
        assert results["slot_2"].returncode == 1

    def test_captures_stdout(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        cmd = [sys.executable, "-c", "print('hello from slot')"]
        results = runner.run(cmd, sync=False)

        assert "hello from slot" in results["slot_1"].output_lines

    def test_fixture_slot_json_in_env(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        script = (
            "import os, json; "
            "data = json.loads(os.environ['LITMUS_FIXTURE_SLOT']); "
            "print(data['slot_id'])"
        )
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False)

        assert results["slot_1"].output_lines[0] == "slot_1"
        assert results["slot_2"].output_lines[0] == "slot_2"


class TestSlotRunnerValidation:
    """Input validation."""

    def test_empty_slots_raises(self):
        with pytest.raises(ValueError, match="At least one slot"):
            SlotRunner({}, {})

    def test_missing_dut_raises(self):
        slots = _make_slots()
        with pytest.raises(ValueError, match="Missing DUT identity"):
            SlotRunner(
                slots,
                {"slot_1": DUT(serial="SN001")},  # slot_2 missing
            )

    def test_extra_env_vars_passed(self):
        slots = _make_slots()
        duts = _make_duts()
        runner = SlotRunner(slots, duts)

        script = "import os; print(os.environ.get('MY_CUSTOM_VAR', 'not_set'))"
        cmd = [sys.executable, "-c", script]
        results = runner.run(cmd, sync=False, env={"MY_CUSTOM_VAR": "hello"})

        assert results["slot_1"].output_lines[0] == "hello"
