"""Tests for shared instrument lifecycle functions."""

from uuid import uuid4

import pytest

from litmus.instruments.lifecycle import (
    disconnect,
    load_and_connect,
    load_driver_class,
    verify_and_wrap,
)
from litmus.models.instrument import InstrumentInfo, InstrumentRecord


class TestLoadDriverClass:
    def test_returns_none_for_none(self):
        assert load_driver_class(None) is None

    def test_returns_none_for_invalid(self):
        assert load_driver_class("nonexistent.module.Class") is None

    def test_loads_valid_class(self):
        cls = load_driver_class("pathlib.Path")
        from pathlib import Path

        assert cls is Path


class TestLoadAndConnect:
    def test_mock_instrument(self):
        record = InstrumentRecord(
            role="dmm",
            instrument_id="dmm-001",
            resource="GPIB::16::INSTR",
            info=InstrumentInfo(manufacturer="Keithley", model="2000"),
        )
        inst = load_and_connect(record, mock=True)
        # Mock stores values in _mock_values; accessing them returns
        # _make_mock_method wrappers. Verify they're stored correctly.
        assert inst._mock_values["manufacturer"] == "Keithley"
        assert inst._mock_values["model"] == "2000"


class TestVerifyAndWrap:
    def test_returns_driver_without_event_log(self):
        record = InstrumentRecord(
            role="dmm",
            instrument_id="dmm-001",
            resource="",
            mocked=True,
        )
        driver = object()
        result = verify_and_wrap(driver, "dmm", record, None, None)
        assert result is driver

    def test_wraps_with_event_log(self, tmp_path):
        from litmus.data.event_log import EventLog

        session_id = uuid4()
        event_log = EventLog(tmp_path / "events", session_id)
        record = InstrumentRecord(
            role="dmm",
            instrument_id="dmm-001",
            resource="",
            mocked=True,
        )
        driver = object()

        from litmus.instruments.observer import DriverObserver, EventEmitter

        emitter = EventEmitter(event_log, session_id, "dmm")
        observer = DriverObserver(object, "dmm", emitter)
        result = verify_and_wrap(driver, "dmm", record, event_log, session_id, observer=observer)

        from litmus.instruments.proxy import InstrumentProxy

        assert isinstance(result, InstrumentProxy)
        event_log.close()


class TestDisconnect:
    def test_disconnect_method(self):
        class FakeInst:
            disconnected = False

            def disconnect(self):
                self.disconnected = True

        inst = FakeInst()
        disconnect(inst, "dmm")
        assert inst.disconnected

    def test_close_method(self):
        class FakeInst:
            closed = False

            def close(self):
                self.closed = True

        inst = FakeInst()
        disconnect(inst, "dmm")
        assert inst.closed

    def test_error_swallowed(self):
        class FakeInst:
            def disconnect(self):
                raise RuntimeError("boom")

        with pytest.warns(UserWarning, match="Failed to cleanup instrument 'dmm'"):
            disconnect(FakeInst(), "dmm")  # Should not raise
