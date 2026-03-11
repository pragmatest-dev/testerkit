"""Tests for ModbusObserver."""

from __future__ import annotations

from litmus.data.events import InstrumentRead, InstrumentSet
from litmus.instruments.observers.modbus import ModbusObserver

from .conftest import make_observer


class TestModbusRead:
    def test_read_holding_registers(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("read_holding_registers", (100, 10), {}, [1, 2, 3])
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentRead)
        assert e.channel_id == "plc.read_holding_registers"

    def test_read_register(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("read_register", (42,), {}, 1234)
        assert len(log.events) == 1
        assert log.events[0].channel_id == "plc.reg_42"

    def test_read_float(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("read_float", (100,), {}, 3.14)
        assert len(log.events) == 1
        assert log.events[0].channel_id == "plc.reg_100"


class TestModbusWrite:
    def test_write_register(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("write_register", (42, 1234), {}, None)
        assert len(log.events) == 1
        e = log.events[0]
        assert isinstance(e, InstrumentSet)
        assert e.channel_id == "plc.write_register"
        assert e.value == 1234

    def test_write_float(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("write_float", (100, 3.14), {}, None)
        assert len(log.events) == 1
        assert log.events[0].channel_id == "plc.reg_100"
        assert log.events[0].value == 3.14


class TestModbusSilent:
    def test_connect_silent(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("connect", (), {}, None)
        assert len(log.events) == 0

    def test_close_silent(self):
        obs, log = make_observer(ModbusObserver, role="plc")
        obs.on_call("close", (), {}, None)
        assert len(log.events) == 0
