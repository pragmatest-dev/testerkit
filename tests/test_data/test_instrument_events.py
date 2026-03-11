"""Tests for instrument event types."""

from __future__ import annotations

import json
from uuid import uuid4

from litmus.data.events import (
    InstrumentConfigure,
    InstrumentDisconnected,
    InstrumentRead,
    InstrumentSet,
)


class TestSerialization:
    def test_instrument_read_roundtrip(self):
        event = InstrumentRead(
            session_id=uuid4(),
            instrument_role="dmm",
            channel_id="dmm.dc_voltage",
            method="measure_dc_voltage",
            value=3.3,
            units="V",
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "instrument.read"
        assert data["value"] == 3.3
        assert data["units"] == "V"

    def test_instrument_set_roundtrip(self):
        event = InstrumentSet(
            session_id=uuid4(),
            instrument_role="psu",
            channel_id="psu.voltage",
            attribute="voltage",
            value=5.0,
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "instrument.set"
        assert data["attribute"] == "voltage"

    def test_instrument_configure_roundtrip(self):
        event = InstrumentConfigure(
            session_id=uuid4(),
            instrument_role="dmm",
            method="configure_range",
            parameters={"auto": True, "range": 10.0},
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "instrument.configure"
        assert data["parameters"]["auto"] is True

    def test_instrument_disconnected_roundtrip(self):
        event = InstrumentDisconnected(
            session_id=uuid4(),
            role="dmm",
            instrument_id="keithley_dmm_001",
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "fixture.instrument_disconnected"
        assert data["role"] == "dmm"
