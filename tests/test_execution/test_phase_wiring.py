"""Tests for the profile-station_type-fixture cross-check.

Exercises ``validate_phase_wiring`` directly (the runner-neutral
helper). Pytest-end-to-end coverage of the same flow is implicit in
``examples/07-profiles`` running green under mocks.
"""

from __future__ import annotations

import pytest

from litmus.execution.profiles import ProfileError, validate_phase_wiring
from litmus.models.project import ProfileConfig
from litmus.models.station import (
    InstrumentConfig,
    StationConfig,
    StationInstrumentConfig,
    StationType,
)
from litmus.models.test_config import FixtureConfig


def _station(station_type: str | None = None, **roles: str) -> StationConfig:
    """Build a minimal mock station with the given role → type entries."""
    instruments = {
        role: StationInstrumentConfig(type=t, driver="drivers.test:Test", mock=True)
        for role, t in roles.items()
    }
    return StationConfig(
        id="bench_test",
        name="Test Bench",
        station_type=station_type,
        instruments=instruments,
    )


def _station_type(**roles: str) -> StationType:
    """Build a station-type template requiring the given role → type entries."""
    instruments = {
        role: InstrumentConfig(type=t, driver="drivers.test:Test") for role, t in roles.items()
    }
    return StationType(
        id="lab_bench",
        description="lab",
        instruments=instruments,
    )


def _fixture(station_types: list[str] | None = None) -> FixtureConfig:
    """Build a minimal fixture with optional station_types."""
    return FixtureConfig(
        id="fix",
        part_id="prod",
        station_types=station_types or [],
        connections={},
    )


def _profile(station_type: str | None = None, fixture: str | None = None) -> ProfileConfig:
    return ProfileConfig(station_type=station_type, fixture=fixture)


class TestStationCompliance:
    def test_compliant_station_passes(self) -> None:
        station = _station(station_type="lab_bench", dmm="DMM", psu="PSU")
        template = _station_type(dmm="DMM", psu="PSU")
        # No profile set; only compliance check fires.
        validate_phase_wiring(
            profile=None,
            station_config=station,
            fixture_config=None,
            station_type_template=template,
        )

    def test_missing_role_raises(self) -> None:
        station = _station(station_type="lab_bench", dmm="DMM")
        template = _station_type(dmm="DMM", psu="PSU")
        with pytest.raises(ProfileError, match="psu"):
            validate_phase_wiring(
                profile=None,
                station_config=station,
                fixture_config=None,
                station_type_template=template,
            )

    def test_missing_template_skips_compliance(self) -> None:
        station = _station(station_type="lab_bench", dmm="DMM")
        # template not loaded — compliance check is skipped, no error.
        validate_phase_wiring(
            profile=None,
            station_config=station,
            fixture_config=None,
            station_type_template=None,
        )


class TestProfileStationTypeMatch:
    def test_matching_types_pass(self) -> None:
        station = _station(station_type="lab_bench", dmm="DMM")
        validate_phase_wiring(
            profile=_profile(station_type="lab_bench"),
            station_config=station,
            fixture_config=None,
            station_type_template=None,
        )

    def test_mismatched_station_type_raises(self) -> None:
        station = _station(station_type="dev_bench", dmm="DMM")
        with pytest.raises(ProfileError, match="dev_bench"):
            validate_phase_wiring(
                profile=_profile(station_type="lab_bench"),
                station_config=station,
                fixture_config=None,
                station_type_template=None,
            )

    def test_profile_without_station_type_skips_match(self) -> None:
        station = _station(station_type="dev_bench")
        # Profile doesn't bind station_type — no cross-check fires.
        validate_phase_wiring(
            profile=_profile(),
            station_config=station,
            fixture_config=None,
            station_type_template=None,
        )


class TestProfileFixtureCompatibility:
    def test_fixture_with_compatible_type_passes(self) -> None:
        validate_phase_wiring(
            profile=_profile(station_type="lab_bench"),
            station_config=_station(station_type="lab_bench"),
            fixture_config=_fixture(["lab_bench", "dev_bench"]),
            station_type_template=None,
        )

    def test_fixture_with_incompatible_types_raises(self) -> None:
        with pytest.raises(ProfileError, match="dev_bench"):
            validate_phase_wiring(
                profile=_profile(station_type="lab_bench"),
                station_config=_station(station_type="lab_bench"),
                fixture_config=_fixture(["dev_bench"]),
                station_type_template=None,
            )

    def test_fixture_with_empty_station_types_skips_check(self) -> None:
        # Empty station_types list = "any station" — cross-check skipped.
        validate_phase_wiring(
            profile=_profile(station_type="lab_bench"),
            station_config=_station(station_type="lab_bench"),
            fixture_config=_fixture([]),
            station_type_template=None,
        )


class TestNoOpCases:
    def test_no_profile_no_station_no_fixture(self) -> None:
        validate_phase_wiring(
            profile=None,
            station_config=None,
            fixture_config=None,
            station_type_template=None,
        )

    def test_only_station_no_template(self) -> None:
        # Station has station_type label but template isn't loaded —
        # compliance check skipped, profile-side checks no-op (no profile).
        validate_phase_wiring(
            profile=None,
            station_config=_station(station_type="lab_bench"),
            fixture_config=None,
            station_type_template=None,
        )


class TestProfileCascade:
    """Profile cascade should merge station_type / fixture last-wins."""

    def test_child_overrides_parent_station_type(self) -> None:
        from litmus.execution.profiles import flatten_profile_chain
        from litmus.models.project import ProjectConfig

        project = ProjectConfig(
            name="test",
            profiles={
                "parent": ProfileConfig(description="base", station_type="parent_type"),
                "child": ProfileConfig(extends="parent", station_type="child_type"),
            },
        )
        merged = flatten_profile_chain("child", project)
        assert merged.station_type == "child_type"

    def test_child_inherits_parent_fixture_when_unset(self) -> None:
        from litmus.execution.profiles import flatten_profile_chain
        from litmus.models.project import ProjectConfig

        project = ProjectConfig(
            name="test",
            profiles={
                "parent": ProfileConfig(description="base", fixture="parent_fix"),
                "child": ProfileConfig(extends="parent"),
            },
        )
        merged = flatten_profile_chain("child", project)
        assert merged.fixture == "parent_fix"
