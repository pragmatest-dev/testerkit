"""Tests for fixture site resolution."""

import pytest

from litmus.execution.slots import (
    ResolvedSite,
    detect_shared_instruments,
    resolve_fixture_sites,
)
from litmus.models.test_config import FixtureConfig, FixtureConnection, FixtureSite


class TestSingleUUTFixture:
    """Single-UUT fixtures (connections, no sites) produce one implicit site at index 0."""

    def test_single_uut_returns_site_at_index_0(self):
        fc = FixtureConfig(
            id="simple",
            connections={
                "vout": FixtureConnection(name="vout", instrument="dmm"),
            },
        )
        sites = resolve_fixture_sites(fc)
        assert len(sites) == 1
        assert sites[0].site_index == 0
        assert "vout" in sites[0].connections

    def test_single_uut_instrument_roles(self):
        fc = FixtureConfig(
            id="simple",
            connections={
                "vout": FixtureConnection(name="vout", instrument="dmm"),
                "vin": FixtureConnection(name="vin", instrument="psu"),
            },
        )
        sites = resolve_fixture_sites(fc)
        assert sites[0].instrument_roles == {"dmm", "psu"}

    def test_empty_connections_returns_single_site(self):
        fc = FixtureConfig(id="bare")
        sites = resolve_fixture_sites(fc)
        assert len(sites) == 1
        assert sites[0].connections == {}


class TestMultiSiteFixture:
    """Multi-site fixtures produce one ResolvedSite per site."""

    def test_two_sites(self):
        fc = FixtureConfig(
            id="dual",
            sites=[
                FixtureSite(
                    connections={
                        "vout": FixtureConnection(
                            name="vout",
                            instrument="dmm",
                            instrument_channel="1",
                        )
                    },
                ),
                FixtureSite(
                    connections={
                        "vout": FixtureConnection(
                            name="vout",
                            instrument="dmm",
                            instrument_channel="2",
                        )
                    },
                ),
            ],
        )
        sites = resolve_fixture_sites(fc)
        assert len(sites) == 2
        assert sites[0].site_index == 0
        assert sites[1].site_index == 1
        assert sites[0].connections["vout"].instrument_channel == "1"
        assert sites[1].connections["vout"].instrument_channel == "2"

    def test_named_sites(self):
        fc = FixtureConfig(
            id="dual",
            sites=[
                FixtureSite(name="left"),
                FixtureSite(name="right"),
            ],
        )
        sites = resolve_fixture_sites(fc)
        assert sites[0].site_name == "left"
        assert sites[1].site_name == "right"
        assert sites[0].site_index == 0
        assert sites[1].site_index == 1

    def test_site_instrument_roles(self):
        fc = FixtureConfig(
            id="dual",
            sites=[
                FixtureSite(
                    connections={
                        "vout": FixtureConnection(name="vout", instrument="dmm"),
                        "vin": FixtureConnection(name="vin", instrument="psu_left"),
                    },
                ),
                FixtureSite(
                    connections={
                        "vout": FixtureConnection(name="vout", instrument="dmm"),
                        "vin": FixtureConnection(name="vin", instrument="psu_right"),
                    },
                ),
            ],
        )
        sites = resolve_fixture_sites(fc)
        assert sites[0].instrument_roles == {"dmm", "psu_left"}
        assert sites[1].instrument_roles == {"dmm", "psu_right"}

    def test_dedicated_instruments_per_site(self):
        fc = FixtureConfig(
            id="dedicated",
            sites=[
                FixtureSite(
                    connections={"vout": FixtureConnection(name="vout", instrument="dmm_left")},
                ),
                FixtureSite(
                    connections={"vout": FixtureConnection(name="vout", instrument="dmm_right")},
                ),
            ],
        )
        sites = resolve_fixture_sites(fc)
        assert sites[0].instrument_roles == {"dmm_left"}
        assert sites[1].instrument_roles == {"dmm_right"}


class TestFixtureConfigValidation:
    """FixtureConfig rejects invalid combinations."""

    def test_connections_and_sites_both_populated_raises(self):
        with pytest.raises(ValueError, match="cannot have both"):
            FixtureConfig(
                id="bad",
                connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
                sites=[
                    FixtureSite(
                        connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
                    )
                ],
            )

    def test_site_count_single(self):
        fc = FixtureConfig(
            id="simple",
            connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
        )
        assert fc.site_count == 1
        assert not fc.is_multi_site

    def test_site_count_multi(self):
        fc = FixtureConfig(
            id="dual",
            sites=[
                FixtureSite(),
                FixtureSite(),
            ],
        )
        assert fc.site_count == 2
        assert fc.is_multi_site

    def test_single_site_not_multi(self):
        fc = FixtureConfig(
            id="one_site",
            sites=[FixtureSite()],
        )
        assert fc.site_count == 1
        assert not fc.is_multi_site


class TestInstrumentValidation:
    """Site resolution validates instrument references against station."""

    def test_valid_instruments_pass(self):
        fc = FixtureConfig(
            id="valid",
            connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
        )
        # Should not raise
        resolve_fixture_sites(fc, station_instruments={"dmm", "psu"})

    def test_missing_instrument_raises(self):
        fc = FixtureConfig(
            id="bad_ref",
            connections={"vout": FixtureConnection(name="vout", instrument="scope")},
        )
        with pytest.raises(ValueError, match="not in station config.*scope"):
            resolve_fixture_sites(fc, station_instruments={"dmm", "psu"})

    def test_multi_site_missing_instrument_raises(self):
        fc = FixtureConfig(
            id="bad_multi",
            sites=[
                FixtureSite(
                    connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
                ),
                FixtureSite(
                    connections={"vout": FixtureConnection(name="vout", instrument="missing_dmm")},
                ),
            ],
        )
        with pytest.raises(ValueError, match="site 1.*missing_dmm"):
            resolve_fixture_sites(fc, station_instruments={"dmm"})

    def test_no_station_instruments_skips_validation(self):
        fc = FixtureConfig(
            id="any",
            connections={"vout": FixtureConnection(name="vout", instrument="anything")},
        )
        # Should not raise when station_instruments is None
        resolve_fixture_sites(fc, station_instruments=None)


class TestResolvedSiteModel:
    """ResolvedSite is a proper Pydantic model."""

    def test_resolved_site_fields(self):
        site = ResolvedSite(
            site_index=0,
            site_name="left",
            connections={"vout": FixtureConnection(name="vout", instrument="dmm")},
            instrument_roles={"dmm"},
        )
        assert site.site_index == 0
        assert site.site_name == "left"
        assert "vout" in site.connections
        assert "dmm" in site.instrument_roles

    def test_uut_resource_defaults_none(self):
        site = ResolvedSite(site_index=0)
        assert site.uut_resource is None


class TestDetectSharedInstruments:
    """detect_shared_instruments identifies roles used by 2+ sites."""

    def test_no_shared_when_dedicated(self):
        sites = [
            ResolvedSite(site_index=0, instrument_roles={"dmm_left", "psu_left"}),
            ResolvedSite(site_index=1, instrument_roles={"dmm_right", "psu_right"}),
        ]
        assert detect_shared_instruments(sites) == set()

    def test_shared_dmm(self):
        sites = [
            ResolvedSite(site_index=0, instrument_roles={"dmm", "psu_left"}),
            ResolvedSite(site_index=1, instrument_roles={"dmm", "psu_right"}),
        ]
        assert detect_shared_instruments(sites) == {"dmm"}

    def test_multiple_shared(self):
        sites = [
            ResolvedSite(site_index=0, instrument_roles={"dmm", "matrix"}),
            ResolvedSite(site_index=1, instrument_roles={"dmm", "matrix"}),
        ]
        assert detect_shared_instruments(sites) == {"dmm", "matrix"}

    def test_empty_sites(self):
        assert detect_shared_instruments([]) == set()

    def test_single_site(self):
        sites = [ResolvedSite(site_index=0, instrument_roles={"dmm"})]
        assert detect_shared_instruments(sites) == set()

    def test_three_sites_sharing(self):
        sites = [
            ResolvedSite(site_index=0, instrument_roles={"dmm"}),
            ResolvedSite(site_index=1, instrument_roles={"dmm"}),
            ResolvedSite(site_index=2, instrument_roles={"dmm"}),
        ]
        assert detect_shared_instruments(sites) == {"dmm"}
