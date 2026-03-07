"""Tests for InstrumentProxy event emission."""

from __future__ import annotations

from uuid import uuid4

from litmus.data.events import InstrumentConfigure, InstrumentRead, InstrumentSet
from litmus.instruments.proxy import InstrumentProxy


class FakeDriver:
    """Minimal driver for proxy tests."""

    def __init__(self) -> None:
        self.voltage = 0.0
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def measure_dc_voltage(self) -> float:
        return 3.3

    def set_voltage(self, value: float) -> None:
        self.voltage = value

    def configure_range(self, *, auto: bool = True) -> None:
        pass

    def enable_output(self) -> None:
        pass


class CollectingLog:
    """Minimal stand-in for EventLog that collects emitted events."""

    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)


def _make_proxy(driver=None) -> tuple[InstrumentProxy, CollectingLog]:
    log = CollectingLog()
    session_id = uuid4()
    run_id = uuid4()
    proxy = InstrumentProxy(
        driver or FakeDriver(), "dmm", log, session_id, run_id,  # type: ignore[arg-type]
    )
    return proxy, log


class TestReadMethods:
    def test_emits_instrument_read(self):
        proxy, log = _make_proxy()
        result = proxy.measure_dc_voltage()

        assert result == 3.3
        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentRead)
        assert event.instrument_role == "dmm"
        assert event.channel_id == "dmm.dc_voltage"
        assert event.method == "measure_dc_voltage"
        assert event.value == 3.3

    def test_return_value_preserved(self):
        proxy, _ = _make_proxy()
        assert proxy.measure_dc_voltage() == 3.3


class TestSetMethods:
    def test_emits_instrument_set(self):
        proxy, log = _make_proxy()
        proxy.set_voltage(5.0)

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentSet)
        assert event.instrument_role == "dmm"
        assert event.channel_id == "dmm.voltage"
        assert event.attribute == "voltage"
        assert event.value == 5.0


class TestConfigureMethods:
    def test_emits_instrument_configure(self):
        proxy, log = _make_proxy()
        proxy.configure_range(auto=True)

        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentConfigure)
        assert event.instrument_role == "dmm"
        assert event.method == "configure_range"
        assert event.parameters == {"auto": True}

    def test_unrecognized_method_classified_as_configure(self):
        proxy, log = _make_proxy()
        proxy.enable_output()

        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentConfigure)
        assert log.events[0].method == "enable_output"


class TestPassthrough:
    def test_connect_no_event(self):
        proxy, log = _make_proxy()
        proxy.connect()
        assert len(log.events) == 0

    def test_disconnect_no_event(self):
        proxy, log = _make_proxy()
        proxy.disconnect()
        assert len(log.events) == 0

    def test_private_attr_no_event(self):
        proxy, log = _make_proxy()
        assert proxy._connected is False
        assert len(log.events) == 0

    def test_property_access(self):
        proxy, log = _make_proxy()
        assert proxy.voltage == 0.0
        assert len(log.events) == 0


class TestChannelIdDerivation:
    def test_read_strips_measure_prefix(self):
        proxy, log = _make_proxy()
        proxy.measure_dc_voltage()
        assert log.events[0].channel_id == "dmm.dc_voltage"

    def test_set_strips_set_prefix(self):
        proxy, log = _make_proxy()
        proxy.set_voltage(1.0)
        assert log.events[0].channel_id == "dmm.voltage"
