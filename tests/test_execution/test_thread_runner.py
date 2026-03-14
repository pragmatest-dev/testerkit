"""Tests for ThreadSlotRunner thread-per-slot execution."""

import threading
from uuid import uuid4

import pytest

from litmus.data.models import DUT
from litmus.execution.slots import ResolvedSlot
from litmus.execution.thread_runner import ThreadSlotResult, ThreadSlotRunner
from litmus.instruments.shared import SharedInstrumentHandle


class FakeDriver:
    def measure_voltage(self) -> float:
        return 3.3


def _make_slots(count: int = 2) -> dict[str, ResolvedSlot]:
    return {
        f"slot_{i}": ResolvedSlot(slot_id=f"slot_{i}")
        for i in range(1, count + 1)
    }


def _make_duts(slot_ids: list[str]) -> dict[str, DUT]:
    return {sid: DUT(serial=f"SN-{sid}") for sid in slot_ids}


class TestThreadSlotRunner:
    def test_requires_slots(self):
        with pytest.raises(ValueError, match="At least one slot"):
            ThreadSlotRunner({}, {})

    def test_requires_matching_duts(self):
        slots = _make_slots(2)
        duts = {"slot_1": DUT(serial="SN1")}
        with pytest.raises(ValueError, match="Missing DUT"):
            ThreadSlotRunner(slots, duts)

    def test_session_id(self):
        slots = _make_slots(1)
        duts = _make_duts(list(slots.keys()))
        sid = uuid4()
        runner = ThreadSlotRunner(slots, duts, session_id=sid)
        assert runner.session_id == sid

    def test_run_two_slots(self):
        slots = _make_slots(2)
        duts = _make_duts(list(slots.keys()))

        def run_slot(slot_id, slot, dut, shared_handles, **kwargs):
            return ThreadSlotResult(slot_id=slot_id, outcome="pass")

        runner = ThreadSlotRunner(slots, duts)
        results = runner.run(run_slot)

        assert len(results) == 2
        assert results["slot_1"].outcome == "pass"
        assert results["slot_2"].outcome == "pass"

    def test_run_with_failure(self):
        slots = _make_slots(2)
        duts = _make_duts(list(slots.keys()))

        def run_slot(slot_id, slot, dut, shared_handles, **kwargs):
            if slot_id == "slot_2":
                return ThreadSlotResult(slot_id=slot_id, outcome="fail", error="test fail")
            return ThreadSlotResult(slot_id=slot_id, outcome="pass")

        runner = ThreadSlotRunner(slots, duts)
        results = runner.run(run_slot)

        assert results["slot_1"].outcome == "pass"
        assert results["slot_2"].outcome == "fail"
        assert results["slot_2"].error == "test fail"

    def test_run_with_exception(self):
        slots = _make_slots(1)
        duts = _make_duts(list(slots.keys()))

        def run_slot(slot_id, slot, dut, shared_handles, **kwargs):
            raise RuntimeError("boom")

        runner = ThreadSlotRunner(slots, duts)
        results = runner.run(run_slot)

        assert results["slot_1"].outcome == "error"
        assert "boom" in results["slot_1"].error

    def test_shared_handles_passed_to_slots(self):
        slots = _make_slots(2)
        duts = _make_duts(list(slots.keys()))

        driver = FakeDriver()
        handle = SharedInstrumentHandle("dmm", driver, threading.Lock())
        shared_handles = {"dmm": handle}

        received_handles: list[dict] = []

        def run_slot(slot_id, slot, dut, shared_handles, **kwargs):
            received_handles.append(shared_handles)
            return ThreadSlotResult(slot_id=slot_id, outcome="pass")

        runner = ThreadSlotRunner(slots, duts, shared_handles=shared_handles)
        runner.run(run_slot)

        assert len(received_handles) == 2
        for h in received_handles:
            assert "dmm" in h

    def test_threads_run_in_parallel(self):
        """Verify threads actually run concurrently."""
        slots = _make_slots(2)
        duts = _make_duts(list(slots.keys()))

        barrier = threading.Barrier(2, timeout=5)

        def run_slot(slot_id, slot, dut, shared_handles, **kwargs):
            barrier.wait()  # Both threads must arrive for this to complete
            return ThreadSlotResult(slot_id=slot_id, outcome="pass")

        runner = ThreadSlotRunner(slots, duts)
        results = runner.run(run_slot)

        assert all(r.outcome == "pass" for r in results.values())
