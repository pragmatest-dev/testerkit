"""Tests for fixture slot resolution."""

import pytest

from litmus.execution.slots import (
    DEFAULT_SLOT_ID,
    ResolvedSlot,
    detect_shared_instruments,
    resolve_fixture_slots,
)
from litmus.models.test_config import FixtureConfig, FixtureConnection, FixtureSlot


class TestSingleUUTFixture:
    """Single-UUT fixtures (connections, no slots) produce one implicit slot."""

    def test_single_uut_returns_default_slot(self):
        fc = FixtureConfig(
            id="simple",
            connections={
                "vout": FixtureConnection(name="vout", instrument="dmm"),
            },
        )
        slots = resolve_fixture_slots(fc)
        assert len(slots) == 1
        assert DEFAULT_SLOT_ID in slots
        assert "vout" in slots[DEFAULT_SLOT_ID].connections

    def test_single_uut_instrument_roles(self):
        fc = FixtureConfig(
            id="simple",
            connections={
                "vout": FixtureConnection(name="vout", instrument="dmm"),
                "vin": FixtureConnection(name="vin", instrument="psu"),
            },
        )
        slots = resolve_fixture_slots(fc)
        assert slots[DEFAULT_SLOT_ID].instrument_roles == {"dmm", "psu"}

    def test_empty_connections_returns_empty_slot(self):
        fc = FixtureConfig(id="bare")
        slots = resolve_fixture_slots(fc)
        assert len(slots) == 1
        assert slots[DEFAULT_SLOT_ID].connections == {}


class TestMultiSlotFixture:
    """Multi-slot fixtures produce one ResolvedSlot per slot."""

    def test_two_slots(self):
        fc = FixtureConfig(
            id="dual",
            slots={
                "slot_1": FixtureSlot(
                    connections={
                        "vout": FixtureConnection(
                            name="vout",
                            instrument="dmm",
                            instrument_channel="1",
                        )
                    },
                ),
                "slot_2": FixtureSlot(
                    connections={
                        "vout": FixtureConnection(
                            name="vout",
                            instrument="dmm",
                            instrument_channel="2",
                        )
                    },
                ),
            },
        )
        slots = resolve_fixture_slots(fc)
        assert len(slots) == 2
        assert "slot_1" in slots
        assert "slot_2" in slots
        assert slots["slot_1"].connections["vout"].instrument_channel == "1"
        assert slots["slot_2"].connections["vout"].instrument_channel == "2"

    def test_slot_instrument_roles(self):
        fc = FixtureConfig(
            id="dual",
            slots={
                "slot_1": FixtureSlot(
                    connections={
                        "vout": FixtureConnection(name="vout", instrument="dmm"),
                        "vin": FixtureConnection(name="vin", instrument="psu_left"),
                    },
                ),
                "slot_2": FixtureSlot(
                    connections={
                        "vout": FixtureConnection(name="vout", instrument="dmm"),
                        "vin": FixtureConnection(name="vin", instrument="psu_right"),
                    },
                ),
            },
        )
        slots = resolve_fixture_slots(fc)
        assert slots["slot_1"].instrument_roles == {"dmm", "psu_left"}
        assert slots["slot_2"].instrument_roles == {"dmm", "psu_right"}

    def test_dedicated_instruments_per_slot(self):
        fc = FixtureConfig(
            id="dedicated",
            slots={
                "slot_1": FixtureSlot(
                    connections={"vout": FixtureConnection(name="vout", instrument="dmm_left")},
                ),
                "slot_2": FixtureSlot(
                    connections={"vout": FixtureConnection(name="vout", instrument="dmm_right")},
                ),
            },
        )
        slots = resolve_fixture_slots(fc)
        assert slots["slot_1"].instrument_roles == {"dmm_left"}
        assert slots["slot_2"].instrument_roles == {"dmm_right"}


class TestFixtureConfigValidation:
    """FixtureConfig rejects invalid combinations."""

    def test_connections_and_slots_both_populated_raises(self):
        with pytest.raises(ValueError, match="cannot have both"):
            FixtureConfig(
                id="bad",
                connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
                slots={
                    "slot_1": FixtureSlot(
                        connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
                    )
                },
            )

    def test_slot_count_single(self):
        fc = FixtureConfig(
            id="simple",
            connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
        )
        assert fc.slot_count == 1
        assert not fc.is_multi_slot

    def test_slot_count_multi(self):
        fc = FixtureConfig(
            id="dual",
            slots={
                "slot_1": FixtureSlot(),
                "slot_2": FixtureSlot(),
            },
        )
        assert fc.slot_count == 2
        assert fc.is_multi_slot

    def test_single_slot_not_multi(self):
        fc = FixtureConfig(
            id="one_slot",
            slots={"slot_1": FixtureSlot()},
        )
        assert fc.slot_count == 1
        assert not fc.is_multi_slot


class TestInstrumentValidation:
    """Slot resolution validates instrument references against station."""

    def test_valid_instruments_pass(self):
        fc = FixtureConfig(
            id="valid",
            connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
        )
        # Should not raise
        resolve_fixture_slots(fc, station_instruments={"dmm", "psu"})

    def test_missing_instrument_raises(self):
        fc = FixtureConfig(
            id="bad_ref",
            connections={"vout": FixtureConnection(name="vout", instrument="scope")},
        )
        with pytest.raises(ValueError, match="not in station config.*scope"):
            resolve_fixture_slots(fc, station_instruments={"dmm", "psu"})

    def test_multi_slot_missing_instrument_raises(self):
        fc = FixtureConfig(
            id="bad_multi",
            slots={
                "slot_1": FixtureSlot(
                    connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
                ),
                "slot_2": FixtureSlot(
                    connections={"vout": FixtureConnection(name="vout", instrument="missing_dmm")},
                ),
            },
        )
        with pytest.raises(ValueError, match="slot_2.*missing_dmm"):
            resolve_fixture_slots(fc, station_instruments={"dmm"})

    def test_no_station_instruments_skips_validation(self):
        fc = FixtureConfig(
            id="any",
            connections={"vout": FixtureConnection(name="vout", instrument="anything")},
        )
        # Should not raise when station_instruments is None
        resolve_fixture_slots(fc, station_instruments=None)


class TestResolvedSlotModel:
    """ResolvedSlot is a proper Pydantic model."""

    def test_resolved_slot_fields(self):
        slot = ResolvedSlot(
            slot_id="slot_1",
            connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
            instrument_roles={"dmm"},
        )
        assert slot.slot_id == "slot_1"
        assert "vout" in slot.connections
        assert "dmm" in slot.instrument_roles

    def test_uut_resource_defaults_none(self):
        slot = ResolvedSlot(slot_id="slot_1")
        assert slot.uut_resource is None


class TestDetectSharedInstruments:
    """detect_shared_instruments identifies roles used by 2+ slots."""

    def test_no_shared_when_dedicated(self):
        slots = {
            "slot_1": ResolvedSlot(
                slot_id="slot_1",
                instrument_roles={"dmm_left", "psu_left"},
            ),
            "slot_2": ResolvedSlot(
                slot_id="slot_2",
                instrument_roles={"dmm_right", "psu_right"},
            ),
        }
        assert detect_shared_instruments(slots) == set()

    def test_shared_dmm(self):
        slots = {
            "slot_1": ResolvedSlot(
                slot_id="slot_1",
                instrument_roles={"dmm", "psu_left"},
            ),
            "slot_2": ResolvedSlot(
                slot_id="slot_2",
                instrument_roles={"dmm", "psu_right"},
            ),
        }
        assert detect_shared_instruments(slots) == {"dmm"}

    def test_multiple_shared(self):
        slots = {
            "slot_1": ResolvedSlot(
                slot_id="slot_1",
                instrument_roles={"dmm", "matrix"},
            ),
            "slot_2": ResolvedSlot(
                slot_id="slot_2",
                instrument_roles={"dmm", "matrix"},
            ),
        }
        assert detect_shared_instruments(slots) == {"dmm", "matrix"}

    def test_empty_slots(self):
        assert detect_shared_instruments({}) == set()

    def test_single_slot(self):
        slots = {
            "slot_1": ResolvedSlot(
                slot_id="slot_1",
                instrument_roles={"dmm"},
            ),
        }
        assert detect_shared_instruments(slots) == set()

    def test_three_slots_sharing(self):
        slots = {
            "slot_1": ResolvedSlot(slot_id="slot_1", instrument_roles={"dmm"}),
            "slot_2": ResolvedSlot(slot_id="slot_2", instrument_roles={"dmm"}),
            "slot_3": ResolvedSlot(slot_id="slot_3", instrument_roles={"dmm"}),
        }
        assert detect_shared_instruments(slots) == {"dmm"}
