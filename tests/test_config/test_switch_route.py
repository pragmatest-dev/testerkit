"""Tests for SwitchRoute model and FixtureConnection.route field."""

import pytest
from pydantic import ValidationError

from litmus.models.test_config import FixtureConnection, SwitchRoute


class TestSwitchRoute:
    def test_minimal(self):
        route = SwitchRoute(switch="matrix", channels=["r0c0"])
        assert route.switch == "matrix"
        assert route.channels == ["r0c0"]
        assert route.settling_ms == 0

    def test_with_settling(self):
        route = SwitchRoute(switch="matrix", channels=["r0c0"], settling_ms=10.5)
        assert route.settling_ms == 10.5

    def test_multiple_channels(self):
        route = SwitchRoute(switch="relay_board", channels=["ch1", "ch2", "ch3"])
        assert len(route.channels) == 3

    def test_empty_channels_allowed(self):
        route = SwitchRoute(switch="matrix", channels=[])
        assert route.channels == []

    def test_missing_switch_raises(self):
        with pytest.raises(ValidationError):
            SwitchRoute(channels=["r0c0"])  # type: ignore[call-arg]

    def test_missing_channels_raises(self):
        with pytest.raises(ValidationError):
            SwitchRoute(switch="matrix")  # type: ignore[call-arg]


class TestFixtureConnectionWithRoute:
    def test_no_route_default(self):
        conn = FixtureConnection(name="vout", instrument="dmm")
        assert conn.route is None

    def test_with_route(self):
        conn = FixtureConnection(
            name="vout_measure",
            instrument="dmm",
            instrument_channel="1",
            uut_pin="VOUT",
            route=SwitchRoute(switch="matrix", channels=["r0c0"], settling_ms=5),
        )
        assert conn.route is not None
        assert conn.route.switch == "matrix"
        assert conn.route.channels == ["r0c0"]
        assert conn.route.settling_ms == 5

    def test_round_trip_dict(self):
        conn = FixtureConnection(
            name="vout_measure",
            instrument="dmm",
            uut_pin="VOUT",
            route=SwitchRoute(switch="matrix", channels=["r0c0"]),
        )
        data = conn.model_dump()
        restored = FixtureConnection.model_validate(data)
        assert restored.route is not None
        assert restored.route.switch == "matrix"
        assert restored.route.channels == ["r0c0"]

    def test_from_dict_with_route(self):
        data = {
            "name": "vout_measure",
            "instrument": "dmm",
            "uut_pin": "VOUT",
            "route": {
                "switch": "matrix",
                "channels": ["r0c0", "r0c1"],
                "settling_ms": 10,
            },
        }
        conn = FixtureConnection.model_validate(data)
        assert conn.route is not None
        assert conn.route.settling_ms == 10
