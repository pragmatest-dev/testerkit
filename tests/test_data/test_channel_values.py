"""Tests for channel values extraction logic."""

from __future__ import annotations

from litmus.ui.components.channel_values import extract_channel_points


class TestExtractChannelPoints:
    def test_extracts_instrument_read(self):
        events = [
            {
                "event_type": "instrument.read",
                "channel_id": "dmm.voltage",
                "value": 3.3,
                "units": "V",
                "occurred_at": "2026-03-06T10:00:00Z",
            }
        ]
        result = extract_channel_points(events)
        assert "dmm.voltage" in result
        assert len(result["dmm.voltage"]) == 1
        ts, val, units = result["dmm.voltage"][0]
        assert val == 3.3
        assert units == "V"

    def test_extracts_instrument_set(self):
        events = [
            {
                "event_type": "instrument.set",
                "channel_id": "psu.voltage",
                "value": 5.0,
                "units": "V",
                "occurred_at": "2026-03-06T10:00:00Z",
            }
        ]
        result = extract_channel_points(events)
        assert "psu.voltage" in result
        assert result["psu.voltage"][0][1] == 5.0

    def test_skips_non_instrument_events(self):
        events = [
            {"event_type": "test.measurement", "channel_id": "x", "value": 1.0},
            {"event_type": "session.started", "station_id": "S1"},
        ]
        result = extract_channel_points(events)
        assert result == {}

    def test_skips_missing_channel_id(self):
        events = [
            {"event_type": "instrument.read", "value": 3.3},
        ]
        assert extract_channel_points(events) == {}

    def test_skips_missing_value(self):
        events = [
            {"event_type": "instrument.read", "channel_id": "dmm.voltage"},
        ]
        assert extract_channel_points(events) == {}

    def test_skips_non_numeric_value(self):
        events = [
            {
                "event_type": "instrument.read",
                "channel_id": "dmm.voltage",
                "value": "not_a_number",
            },
        ]
        assert extract_channel_points(events) == {}

    def test_multiple_channels(self):
        events = [
            {
                "event_type": "instrument.read",
                "channel_id": "dmm.voltage",
                "value": 3.3,
                "occurred_at": "2026-03-06T10:00:00Z",
            },
            {
                "event_type": "instrument.set",
                "channel_id": "psu.voltage",
                "value": 5.0,
                "occurred_at": "2026-03-06T10:00:01Z",
            },
        ]
        result = extract_channel_points(events)
        assert len(result) == 2
        assert len(result["dmm.voltage"]) == 1
        assert len(result["psu.voltage"]) == 1

    def test_empty_events(self):
        assert extract_channel_points([]) == {}

    def test_uses_received_at_fallback(self):
        events = [
            {
                "event_type": "instrument.read",
                "channel_id": "dmm.voltage",
                "value": 3.3,
                "received_at": "2026-03-06T10:00:00Z",
            },
        ]
        result = extract_channel_points(events)
        assert result["dmm.voltage"][0][0] == "2026-03-06T10:00:00Z"
