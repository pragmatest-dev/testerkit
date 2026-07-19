"""Tests for instrument event types."""

from __future__ import annotations

import json
from uuid import uuid4

from testerkit.data.event_log import EVENT_LOG_SCHEMA_VERSION
from testerkit.data.events import (
    EVENT_CATALOG_VERSION,
    ChannelStarted,
    InstrumentConfigure,
    InstrumentDisconnected,
    InstrumentReleased,
    InstrumentReserved,
    InstrumentSet,
)


class TestSerialization:
    def test_channel_started_roundtrip(self):
        # Position 2 / v0.2.0: per-sample InstrumentRead was retired.
        # ChannelStarted fires once per (channel_id, session_id) on
        # first write. Carries the instrument identity when source is
        # an instrument observer; sample data lives in ChannelStore.
        event = ChannelStarted(
            session_id=uuid4(),
            channel_id="dmm.dc_voltage",
            instrument_role="dmm",
            method="measure_dc_voltage",
            resource="GPIB0::22::INSTR",
            unit="V",
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "channel.started"
        assert data["channel_id"] == "dmm.dc_voltage"
        assert data["instrument_role"] == "dmm"
        assert data["method"] == "measure_dc_voltage"
        assert data["unit"] == "V"

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

    def test_instrument_reserved_roundtrip(self):
        event = InstrumentReserved(
            session_id=uuid4(),
            role="dmm",
            instrument_id="keithley_dmm_001",
            resource="GPIB0::22::INSTR",
            waited_ms=12.5,
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "instrument.reserved"
        assert data["role"] == "dmm"
        assert data["instrument_id"] == "keithley_dmm_001"
        assert data["resource"] == "GPIB0::22::INSTR"
        assert data["waited_ms"] == 12.5

    def test_instrument_reserved_uncontended_waited_ms_zero(self):
        event = InstrumentReserved(
            session_id=uuid4(),
            role="psu",
            instrument_id="psu-001",
            resource="GPIB0::5::INSTR",
            waited_ms=0.0,
        )
        data = json.loads(event.model_dump_json())
        assert data["waited_ms"] == 0.0

    def test_instrument_released_roundtrip(self):
        event = InstrumentReleased(
            session_id=uuid4(),
            role="dmm",
            instrument_id="keithley_dmm_001",
            resource="GPIB0::22::INSTR",
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "instrument.released"
        assert data["role"] == "dmm"
        assert data["instrument_id"] == "keithley_dmm_001"
        assert data["resource"] == "GPIB0::22::INSTR"


def test_event_schema_versions_are_baseline():
    # Events carries two coordinates (§3); both start on the 0.1 baseline (§2).
    assert EVENT_LOG_SCHEMA_VERSION == "0.1"  # storage envelope
    assert EVENT_CATALOG_VERSION == "0.1"  # payload catalog
