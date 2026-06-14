"""Tests for PyMeasureObserver descriptor introspection."""

from __future__ import annotations

from uuid import uuid4

from litmus.data.events import ChannelStarted, InstrumentSet
from litmus.instruments.observer import InstrumentEventEmitter
from litmus.instruments.observers.pymeasure import PyMeasureObserver, build_channel_map
from litmus.models.instrument import ChannelKind


class CollectingLog:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


# --- Test driver classes ---


class ReadOnlyProp:
    @property
    def voltage(self) -> float:
        return 3.3


class ReadWriteProp:
    @property
    def voltage(self) -> float:
        return 3.3

    @voltage.setter
    def voltage(self, v: float) -> None:
        pass


class WriteOnlyProp:
    voltage = property(fget=None, fset=lambda self, v: None)


class PyMeasureLikeMeasurement:
    class _Desc:
        def __get__(self, obj, objtype=None):  # noqa: ANN001, ANN204
            return 3.3

    voltage = _Desc()


class PyMeasureLikeControl:
    class _Desc:
        def __get__(self, obj, objtype=None):  # noqa: ANN001, ANN204
            return 5.0

        def __set__(self, obj, value):  # noqa: ANN001, ANN204
            pass

    voltage = _Desc()


class MethodDriver:
    def measure_voltage(self) -> float:
        return 3.3

    def set_voltage(self, v: float) -> None:
        pass


class MixedDriver:
    @property
    def voltage(self) -> float:
        return 3.3

    @voltage.setter
    def voltage(self, v: float) -> None:
        pass

    def measure_current(self) -> float:
        return 0.001


class InheritedDriver(ReadOnlyProp):
    @property
    def current(self) -> float:
        return 0.001


class PrivateAttrDriver:
    @property
    def voltage(self) -> float:
        return 3.3

    @property
    def _internal(self) -> int:
        return 42


# --- build_channel_map tests ---


class TestBuildChannelMap:
    def test_read_only_property(self):
        m = build_channel_map(ReadOnlyProp)
        assert m["voltage"] == ChannelKind.read

    def test_read_write_property(self):
        m = build_channel_map(ReadWriteProp)
        assert m["voltage"] == ChannelKind.control

    def test_write_only_property(self):
        m = build_channel_map(WriteOnlyProp)
        assert m["voltage"] == ChannelKind.set

    def test_pymeasure_measurement(self):
        m = build_channel_map(PyMeasureLikeMeasurement)
        assert m["voltage"] == ChannelKind.read

    def test_pymeasure_control(self):
        m = build_channel_map(PyMeasureLikeControl)
        assert m["voltage"] == ChannelKind.control

    def test_methods_not_in_map(self):
        m = build_channel_map(MethodDriver)
        assert "measure_voltage" not in m
        assert "set_voltage" not in m

    def test_mixed(self):
        m = build_channel_map(MixedDriver)
        assert m["voltage"] == ChannelKind.control
        assert "measure_current" not in m

    def test_inheritance(self):
        m = build_channel_map(InheritedDriver)
        assert m["voltage"] == ChannelKind.read
        assert m["current"] == ChannelKind.read

    def test_private_excluded(self):
        m = build_channel_map(PrivateAttrDriver)
        assert "voltage" in m
        assert "_internal" not in m

    def test_yaml_overrides(self):
        m = build_channel_map(ReadOnlyProp, {"voltage": "control"})
        assert m["voltage"] == ChannelKind.control

    def test_yaml_adds_new(self):
        m = build_channel_map(ReadOnlyProp, {"current": "read"})
        assert m["current"] == ChannelKind.read
        assert m["voltage"] == ChannelKind.read


# --- PyMeasureObserver tests ---


def _make_observer(
    driver_class: type,
    overrides: dict[str, str] | None = None,
) -> tuple[PyMeasureObserver, CollectingLog]:
    log = CollectingLog()
    emitter = InstrumentEventEmitter(event_log=log, session_id=uuid4(), role="dmm")  # type: ignore[arg-type]
    obs = PyMeasureObserver(driver_class, "dmm", emitter, yaml_overrides=overrides)
    return obs, log


class TestPyMeasureObserverGetattr:
    def test_read_property_emits_read(self):
        obs, log = _make_observer(ReadOnlyProp)
        result = obs.on_getattr("voltage", 3.3)
        assert result == 3.3
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "dmm.voltage"

    def test_control_property_emits_read(self):
        obs, log = _make_observer(ReadWriteProp)
        result = obs.on_getattr("voltage", 3.3)
        assert result == 3.3
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)

    def test_unmapped_attr_no_event(self):
        obs, log = _make_observer(ReadOnlyProp)
        result = obs.on_getattr("unknown_thing", 42)
        assert result == 42
        assert len(log.events) == 0


class TestPyMeasureObserverSetattr:
    def test_control_property_emits_set(self):
        obs, log = _make_observer(ReadWriteProp)
        obs.on_setattr("voltage", 5.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
        assert log.events[0].channel_id == "dmm.voltage"
        assert log.events[0].value == 5.0

    def test_read_only_no_set_event(self):
        obs, log = _make_observer(ReadOnlyProp)
        obs.on_setattr("voltage", 5.0)
        assert len(log.events) == 0


class TestPyMeasureObserverCall:
    def test_unmapped_method_uses_prefix(self):
        obs, log = _make_observer(MixedDriver)
        obs.on_call("measure_current", (), {}, 0.001)
        assert len(log.events) == 1
        assert isinstance(log.events[0], ChannelStarted)
        assert log.events[0].channel_id == "dmm.current"

    def test_mapped_method_skipped(self):
        """Methods in channel map are skipped (handled by on_getattr/on_setattr)."""
        obs, log = _make_observer(ReadWriteProp)
        obs.on_call("voltage", (), {}, 3.3)
        # voltage is in channel map, so on_call is a no-op
        assert len(log.events) == 0


class TestYamlOverrides:
    def test_override_changes_classification(self):
        obs, log = _make_observer(ReadOnlyProp, {"voltage": "control"})
        obs.on_setattr("voltage", 5.0)
        assert len(log.events) == 1
        assert isinstance(log.events[0], InstrumentSet)
