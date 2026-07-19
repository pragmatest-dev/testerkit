"""Unit tests for ``validate_station_against_type``.

The compliance check is "does the station YAML claim instruments that
cover the roles its declared station_type requires." Pure data check,
no I/O — these tests exercise the comparison logic against
hand-built models.
"""

from __future__ import annotations

import pytest

from testerkit.models.station import (
    InstrumentConfig,
    StationConfig,
    StationInstrumentConfig,
    StationType,
    validate_station_against_type,
)


def _make_station(**instruments: tuple[str, ...]) -> StationConfig:
    """Build a station with the given role → (type[, driver]) entries."""
    inst_configs: dict[str, StationInstrumentConfig] = {}
    for role, args in instruments.items():
        inst_type = args[0]
        driver = args[1] if len(args) > 1 else "drivers.test:Test"
        inst_configs[role] = StationInstrumentConfig(type=inst_type, driver=driver, mock=True)
    return StationConfig(id="bench_test", name="Test Bench", instruments=inst_configs)


def _make_type(**instruments: str) -> StationType:
    """Build a station type whose roles map to ``type:`` strings."""
    inst_configs = {
        role: InstrumentConfig(type=inst_type, driver="drivers.test:Test")
        for role, inst_type in instruments.items()
    }
    return StationType(
        id="test_type",
        description="test station type",
        instruments=inst_configs,
    )


def test_fully_compliant_returns_empty_list() -> None:
    station = _make_station(dmm=("DMM",), psu=("PSU",))
    station_type = _make_type(dmm="DMM", psu="PSU")
    assert validate_station_against_type(station, station_type) == []


def test_extra_instruments_on_station_are_fine() -> None:
    """Station may declare more instruments than the type requires."""
    station = _make_station(dmm=("DMM",), psu=("PSU",), scope=("SCOPE",))
    station_type = _make_type(dmm="DMM", psu="PSU")
    assert validate_station_against_type(station, station_type) == []


def test_missing_role_reported() -> None:
    station = _make_station(dmm=("DMM",))
    station_type = _make_type(dmm="DMM", psu="PSU")
    mismatches = validate_station_against_type(station, station_type)
    assert len(mismatches) == 1
    assert "psu" in mismatches[0]
    assert "not declared" in mismatches[0]


def test_wrong_type_reported() -> None:
    station = _make_station(dmm=("DMM",), psu=("DMM",))  # both DMM, but psu role
    station_type = _make_type(dmm="DMM", psu="PSU")
    mismatches = validate_station_against_type(station, station_type)
    assert len(mismatches) == 1
    assert "psu" in mismatches[0]
    assert "type='DMM'" in mismatches[0]
    assert "type='PSU'" in mismatches[0]


def test_multiple_mismatches_listed() -> None:
    station = _make_station(dmm=("DMM",))
    station_type = _make_type(dmm="DMM", psu="PSU", scope="SCOPE")
    mismatches = validate_station_against_type(station, station_type)
    assert len(mismatches) == 2
    joined = "\n".join(mismatches)
    assert "psu" in joined
    assert "scope" in joined


def test_empty_type_always_compliant() -> None:
    """A station type with no required roles is satisfied by any station."""
    station = _make_station()
    # Station type with empty instruments dict requires nothing.
    station_type = StationType(
        id="empty_type",
        description="no required instruments",
        instruments={},
    )
    assert validate_station_against_type(station, station_type) == []


def test_unused_argument_marker() -> None:
    """Sanity: pytest is the test runner reference (silences linter)."""
    assert pytest.__version__
