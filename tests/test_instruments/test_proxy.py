"""Tests for InstrumentProxy event emission via observers."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from litmus.data.event_log import EventLog
from litmus.data.events import ChannelStarted, InstrumentConfigure, InstrumentSet
from litmus.instruments.observer import InstrumentEventBuilder
from litmus.instruments.observers.generic import GenericObserver
from litmus.instruments.observers.pymeasure import PyMeasureObserver
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

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


def _make_proxy(driver=None) -> tuple[InstrumentProxy, CollectingLog]:  # noqa: ANN001
    log = CollectingLog()
    session_id = uuid4()
    run_id = uuid4()
    d = driver or FakeDriver()
    emitter = InstrumentEventBuilder(
        event_log=cast(EventLog, log),
        session_id=session_id,
        role="dmm",  # type: ignore[arg-type]
        run_id=run_id,
    )
    observer = GenericObserver(type(d), "dmm", emitter)
    proxy = InstrumentProxy(d, "dmm", observer)
    return proxy, log


class TestReadMethods:
    def test_emits_instrument_read(self):
        proxy, log = _make_proxy()
        result = proxy.measure_dc_voltage()

        assert result == 3.3
        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, ChannelStarted)
        assert event.instrument_role == "dmm"
        assert event.channel_id == "dmm.dc_voltage"
        assert event.method == "measure_dc_voltage"

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

    def test_unrecognized_method_is_silent(self):
        proxy, log = _make_proxy()
        proxy.enable_output()

        assert len(log.events) == 0


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
        """Non-callable attrs pass through GenericObserver without events."""
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


# --- Property/descriptor-based proxy tests (PyMeasureObserver) ---


class PropertyDriver:
    """Driver with properties instead of prefixed methods."""

    _voltage: float = 3.3
    _current: float = 0.001

    @property
    def voltage(self) -> float:
        return self._voltage

    @voltage.setter
    def voltage(self, v: float) -> None:
        self._voltage = v

    @property
    def current(self) -> float:
        return self._current


def _make_property_proxy() -> tuple[InstrumentProxy, CollectingLog]:
    log = CollectingLog()
    session_id = uuid4()
    emitter = InstrumentEventBuilder(
        event_log=cast(EventLog, log),
        session_id=session_id,
        role="dmm",  # type: ignore[arg-type]
    )
    observer = PyMeasureObserver(PropertyDriver, "dmm", emitter)
    proxy = InstrumentProxy(PropertyDriver(), "dmm", observer)
    return proxy, log


class TestPropertyRead:
    def test_get_control_emits_read(self):
        proxy, log = _make_property_proxy()
        v = proxy.voltage
        assert v == 3.3
        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, ChannelStarted)
        assert event.channel_id == "dmm.voltage"

    def test_get_read_emits_read(self):
        proxy, log = _make_property_proxy()
        c = proxy.current
        assert c == 0.001
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "dmm.current"


class TestPropertySet:
    def test_set_control_emits_set(self):
        proxy, log = _make_property_proxy()
        proxy.voltage = 5.0
        assert len(log.events) == 1
        event = log.events[0]
        assert isinstance(event, InstrumentSet)
        assert event.channel_id == "dmm.voltage"
        assert event.attribute == "voltage"
        assert event.value == 5.0

    def test_set_actually_sets_value(self):
        proxy, _ = _make_property_proxy()
        proxy.voltage = 5.0
        assert proxy.voltage == 5.0


class TestMixedMethodAndProperty:
    def test_method_still_works_with_channel_map(self):
        """Methods not in channel map still use prefix fallback."""

        class MixedDriver(PropertyDriver):
            def measure_temperature(self) -> float:
                return 25.0

        log = CollectingLog()
        emitter = InstrumentEventBuilder(
            event_log=cast(EventLog, log),
            session_id=uuid4(),
            role="dmm",  # type: ignore[arg-type]
        )
        observer = PyMeasureObserver(MixedDriver, "dmm", emitter)
        proxy = InstrumentProxy(MixedDriver(), "dmm", observer)
        t = proxy.measure_temperature()
        assert t == 25.0
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].method == "measure_temperature"
