"""Tests for QCodesObserver."""

from __future__ import annotations

from litmus.data.events import InstrumentConfigure, InstrumentRead
from litmus.instruments.observers.qcodes import QCodesObserver

from .conftest import make_observer


class _FakeParameter:
    """Duck-typed QCodes Parameter."""

    def get(self) -> float:
        return 3.3

    def set(self, value: float) -> None:
        pass


class DescriptorDriver:
    @property
    def voltage(self) -> float:
        return 3.3

    @voltage.setter
    def voltage(self, v: float) -> None:
        pass


class TestQCodesGetattr:
    def test_parameter_object_no_emit(self):
        obs, log = make_observer(QCodesObserver, role="qc")
        param = _FakeParameter()
        result = obs.on_getattr("voltage", param)
        assert result is param
        assert len(log.events) == 0

    def test_descriptor_emits_read(self):
        obs, log = make_observer(QCodesObserver, driver_class=DescriptorDriver, role="qc")
        obs.on_getattr("voltage", 3.3)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)
        assert log.events[0].channel_id == "qc.voltage"

    def test_unmapped_no_emit(self):
        obs, log = make_observer(QCodesObserver, role="qc")
        obs.on_getattr("unknown", 42)
        assert len(log.events) == 0


class TestQCodesCall:
    def test_snapshot_emits_configure(self):
        obs, log = make_observer(QCodesObserver, role="qc")
        obs.on_call("snapshot", (), {"update": True}, {"params": {}})
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)
        assert log.events[0].method == "snapshot"

    def test_prefix_fallback(self):
        obs, log = make_observer(QCodesObserver, role="qc")
        obs.on_call("measure_voltage", (), {}, 3.3)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentRead)


class TestQCodesInstanceParams:
    def test_parameter_names_from_instance(self):
        instance = type("Inst", (), {"parameters": {"voltage": None, "current": None}})()
        obs, log = make_observer(QCodesObserver, role="qc", driver_instance=instance)
        assert obs._parameter_names == {"voltage", "current"}  # type: ignore[attr-defined]
