"""Tests for RouteManager lifecycle, conflict detection, and locking."""

import pytest

from litmus.instruments.route_manager import RouteConflictError, RouteManager
from litmus.models.test_config import FixtureConnection, SwitchRoute


class FakeSwitch:
    """Minimal SwitchDriver implementation for testing."""

    def __init__(self):
        self.closed: list[list[str]] = []
        self.opened: list[list[str]] = []
        self.all_opened = False

    def close_channels(self, channels: list[str]) -> None:
        self.closed.append(channels)

    def open_channels(self, channels: list[str]) -> None:
        self.opened.append(channels)

    def open_all(self) -> None:
        self.all_opened = True


class FakeInstrument:
    """Minimal instrument for testing."""

    def __init__(self, resource: str = "GPIB::16::INSTR"):
        self.resource = resource

    def measure_voltage(self) -> float:
        return 3.3


def _make_connections(**overrides):
    """Build fixture connections with routes for testing."""
    defaults = {
        "vout_measure": FixtureConnection(
            name="vout_measure",
            instrument="dmm",
            instrument_channel="1",
            uut_pin="VOUT",
            route=SwitchRoute(switch="matrix", channels=["r0c0"]),
        ),
    }
    defaults.update(overrides)
    return defaults


def _make_manager(connections=None, instruments=None, **kwargs):
    connections = connections or _make_connections()
    instruments = instruments or {
        "dmm": FakeInstrument(),
        "matrix": FakeSwitch(),
    }
    return RouteManager(connections=connections, instruments=instruments, **kwargs)


@pytest.fixture(autouse=True)
def _use_tmp_lock_dir(tmp_path, monkeypatch):
    """Redirect lock dir to tmp_path for test isolation."""
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))


class TestHasRoutes:
    def test_has_routes_true(self):
        rm = _make_manager()
        assert rm.has_routes is True

    def test_has_routes_false(self):
        connections = {
            "vout": FixtureConnection(name="vout", instrument="dmm", uut_pin="VOUT"),
        }
        rm = _make_manager(connections=connections)
        assert rm.has_routes is False


class TestActivateDeactivate:
    def test_activate_closes_channels(self):
        switch = FakeSwitch()
        rm = _make_manager(instruments={"dmm": FakeInstrument(), "matrix": switch})
        rm.activate("vout_measure")

        assert switch.closed == [["r0c0"]]
        assert "vout_measure" in rm.active_routes

    def test_activate_noop_if_already_active(self):
        switch = FakeSwitch()
        rm = _make_manager(instruments={"dmm": FakeInstrument(), "matrix": switch})
        rm.activate("vout_measure")
        rm.activate("vout_measure")

        assert len(switch.closed) == 1  # Only one close call

    def test_deactivate_opens_channels(self):
        switch = FakeSwitch()
        rm = _make_manager(instruments={"dmm": FakeInstrument(), "matrix": switch})
        rm.activate("vout_measure")
        rm.deactivate("vout_measure")

        assert switch.opened == [["r0c0"]]
        assert "vout_measure" not in rm.active_routes

    def test_deactivate_noop_if_not_active(self):
        switch = FakeSwitch()
        rm = _make_manager(instruments={"dmm": FakeInstrument(), "matrix": switch})
        rm.deactivate("vout_measure")  # No error
        assert switch.opened == []

    def test_deactivate_all(self):
        switch = FakeSwitch()
        connections = _make_connections(
            iout_measure=FixtureConnection(
                name="iout_measure",
                instrument="dmm",
                instrument_channel="2",
                uut_pin="IOUT",
                route=SwitchRoute(switch="matrix", channels=["r1c0"]),
            ),
        )
        rm = _make_manager(
            connections=connections,
            instruments={"dmm": FakeInstrument(), "matrix": switch},
        )
        rm.activate("vout_measure")
        rm.activate("iout_measure")
        rm.deactivate_all()

        assert "vout_measure" not in rm.active_routes
        assert "iout_measure" not in rm.active_routes
        assert len(switch.opened) == 2

    def test_activate_nonexistent_connection_raises(self):
        rm = _make_manager()
        with pytest.raises(KeyError, match="not found"):
            rm.activate("nonexistent")

    def test_activate_connection_without_route_raises(self):
        connections = {
            "direct": FixtureConnection(name="direct", instrument="dmm", uut_pin="VIN"),
        }
        rm = _make_manager(connections=connections)
        with pytest.raises(KeyError, match="no switch route"):
            rm.activate("direct")

    def test_activate_missing_switch_raises(self):
        rm = _make_manager(instruments={"dmm": FakeInstrument()})  # No matrix
        with pytest.raises(KeyError, match="not found"):
            rm.activate("vout_measure")

    def test_activate_non_switch_driver_raises(self):
        rm = _make_manager(
            instruments={"dmm": FakeInstrument(), "matrix": FakeInstrument()},
        )
        with pytest.raises(TypeError, match="SwitchDriver"):
            rm.activate("vout_measure")


class TestConflictDetection:
    def test_channel_overlap_raises(self):
        """Two connections sharing the same switch channel can't be simultaneous."""
        connections = {
            "conn_a": FixtureConnection(
                name="conn_a",
                instrument="dmm",
                instrument_channel="1",
                uut_pin="A",
                route=SwitchRoute(switch="matrix", channels=["r0c0"]),
            ),
            "conn_b": FixtureConnection(
                name="conn_b",
                instrument="psu",
                instrument_channel="1",
                uut_pin="B",
                route=SwitchRoute(switch="matrix", channels=["r0c0"]),
            ),
        }
        switch = FakeSwitch()
        instruments = {
            "dmm": FakeInstrument(),
            "psu": FakeInstrument(),
            "matrix": switch,
        }
        rm = _make_manager(connections=connections, instruments=instruments)

        rm.activate("conn_a")
        with pytest.raises(RouteConflictError, match="r0c0"):
            rm.activate("conn_b")

    def test_instrument_channel_conflict_raises(self):
        """Two connections targeting same instrument+channel can't be simultaneous."""
        connections = {
            "conn_a": FixtureConnection(
                name="conn_a",
                instrument="dmm",
                instrument_channel="1",
                uut_pin="A",
                route=SwitchRoute(switch="matrix", channels=["r0c0"]),
            ),
            "conn_b": FixtureConnection(
                name="conn_b",
                instrument="dmm",
                instrument_channel="1",
                uut_pin="B",
                route=SwitchRoute(switch="matrix", channels=["r1c0"]),
            ),
        }
        switch = FakeSwitch()
        instruments = {"dmm": FakeInstrument(), "matrix": switch}
        rm = _make_manager(connections=connections, instruments=instruments)

        rm.activate("conn_a")
        with pytest.raises(RouteConflictError, match="instrument channel"):
            rm.activate("conn_b")

    def test_non_conflicting_simultaneous(self):
        """Different channels on different instruments can be simultaneous."""
        connections = {
            "conn_a": FixtureConnection(
                name="conn_a",
                instrument="dmm",
                instrument_channel="1",
                uut_pin="A",
                route=SwitchRoute(switch="matrix", channels=["r0c0"]),
            ),
            "conn_b": FixtureConnection(
                name="conn_b",
                instrument="dmm",
                instrument_channel="2",
                uut_pin="B",
                route=SwitchRoute(switch="matrix", channels=["r1c0"]),
            ),
        }
        switch = FakeSwitch()
        instruments = {"dmm": FakeInstrument(), "matrix": switch}
        rm = _make_manager(connections=connections, instruments=instruments)

        rm.activate("conn_a")
        rm.activate("conn_b")  # Should not raise

        assert len(rm.active_routes) == 2


class TestForPin:
    def test_context_manager_activates_and_deactivates(self):
        switch = FakeSwitch()
        rm = _make_manager(instruments={"dmm": FakeInstrument(), "matrix": switch})

        with rm.for_pin("VOUT"):
            assert "vout_measure" in rm.active_routes

        assert "vout_measure" not in rm.active_routes
        assert switch.closed == [["r0c0"]]
        assert switch.opened == [["r0c0"]]

    def test_unknown_pin_raises(self):
        rm = _make_manager()
        with pytest.raises(KeyError, match="No routed fixture connection"):
            with rm.for_pin("NONEXISTENT"):
                pass

    def test_context_manager_deactivates_on_exception(self):
        switch = FakeSwitch()
        rm = _make_manager(instruments={"dmm": FakeInstrument(), "matrix": switch})

        with pytest.raises(ValueError, match="test error"):
            with rm.for_pin("VOUT"):
                raise ValueError("test error")

        assert "vout_measure" not in rm.active_routes
        assert switch.opened == [["r0c0"]]


class TestSettling:
    def test_settling_time(self, monkeypatch):
        """Verify settling delay is applied."""
        sleep_calls: list[float] = []
        monkeypatch.setattr("litmus.instruments.route_manager.time.sleep", sleep_calls.append)

        connections = {
            "vout": FixtureConnection(
                name="vout",
                instrument="dmm",
                uut_pin="VOUT",
                route=SwitchRoute(switch="matrix", channels=["r0c0"], settling_ms=50),
            ),
        }
        switch = FakeSwitch()
        rm = _make_manager(
            connections=connections,
            instruments={"dmm": FakeInstrument(), "matrix": switch},
        )
        rm.activate("vout")

        assert len(sleep_calls) == 1
        assert abs(sleep_calls[0] - 0.05) < 1e-9

    def test_no_settling_when_zero(self, monkeypatch):
        sleep_calls: list[float] = []
        monkeypatch.setattr("litmus.instruments.route_manager.time.sleep", sleep_calls.append)

        rm = _make_manager()
        rm.activate("vout_measure")

        assert len(sleep_calls) == 0
