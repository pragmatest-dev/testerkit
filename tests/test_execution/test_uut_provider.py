"""Tests for UUT identity providers."""

import os

import pytest

from litmus.data.models import UUT
from litmus.execution.slots import ResolvedSite
from litmus.execution.uut_provider import (
    CLIUUTProvider,
    EnvironmentUUTProvider,
    UUTProvider,
)


class TestCLIUUTProvider:
    """CLIUUTProvider resolves UUT from CLI-style arguments."""

    def test_single_serial_returns_same_for_all_sites(self):
        provider = CLIUUTProvider(serial="SN001")
        uut0 = provider.get_uut(0)
        uut1 = provider.get_uut(1)
        assert uut0.serial == "SN001"
        assert uut1.serial == "SN001"

    def test_per_site_serials(self):
        provider = CLIUUTProvider(serials={0: "SN001", 1: "SN002"})
        assert provider.get_uut(0).serial == "SN001"
        assert provider.get_uut(1).serial == "SN002"

    def test_per_site_missing_site_raises(self):
        provider = CLIUUTProvider(serials={0: "SN001"})
        with pytest.raises(ValueError, match="No UUT serial for site 2"):
            provider.get_uut(2)

    def test_both_serial_and_serials_raises(self):
        with pytest.raises(ValueError, match="not both"):
            CLIUUTProvider(serial="SN001", serials={0: "SN002"})

    def test_neither_serial_nor_serials_raises(self):
        with pytest.raises(ValueError, match="must be provided"):
            CLIUUTProvider()

    def test_metadata_fields_passed_through(self):
        provider = CLIUUTProvider(
            serial="SN001",
            part_number="PN-100",
            revision="B",
            lot_number="LOT42",
        )
        uut = provider.get_uut(0)
        assert uut.part_number == "PN-100"
        assert uut.revision == "B"
        assert uut.lot_number == "LOT42"

    def test_from_cli_args_single(self):
        provider = CLIUUTProvider.from_cli_args(uut_serial="SN999", uut_serials=None)
        assert provider.get_uut(0).serial == "SN999"
        assert provider.get_uut(1).serial == "SN999"

    def test_from_cli_args_indexed(self):
        provider = CLIUUTProvider.from_cli_args(
            uut_serial=None,
            uut_serials="0=SN001,1=SN002",
        )
        assert provider.get_uut(0).serial == "SN001"
        assert provider.get_uut(1).serial == "SN002"

    def test_from_cli_args_named(self):
        sites = [
            ResolvedSite(site_index=0, site_name="left"),
            ResolvedSite(site_index=1, site_name="right"),
        ]
        provider = CLIUUTProvider.from_cli_args(
            uut_serial=None,
            uut_serials="left=SN001,right=SN002",
            sites=sites,
        )
        assert provider.get_uut(0).serial == "SN001"
        assert provider.get_uut(1).serial == "SN002"

    def test_from_cli_args_positional(self):
        sites = [
            ResolvedSite(site_index=0),
            ResolvedSite(site_index=1),
        ]
        provider = CLIUUTProvider.from_cli_args(
            uut_serial=None,
            uut_serials="SN001,SN002",
            sites=sites,
        )
        assert provider.get_uut(0).serial == "SN001"
        assert provider.get_uut(1).serial == "SN002"

    def test_from_cli_args_positional_without_sites_raises(self):
        with pytest.raises(ValueError, match="multi-site fixture"):
            CLIUUTProvider.from_cli_args(uut_serial=None, uut_serials="SN001,SN002")

    def test_from_cli_args_positional_count_mismatch_raises(self):
        sites = [ResolvedSite(site_index=0), ResolvedSite(site_index=1), ResolvedSite(site_index=2)]
        with pytest.raises(ValueError, match="2 serial.*3 site"):
            CLIUUTProvider.from_cli_args(
                uut_serial=None,
                uut_serials="SN001,SN002",
                sites=sites,
            )

    def test_from_cli_args_bad_named_format_raises(self):
        with pytest.raises(ValueError, match="Invalid --uut-serials format"):
            CLIUUTProvider.from_cli_args(
                uut_serial=None,
                uut_serials="0=SN001,SN002",  # mixed indexed/positional
            )

    def test_from_cli_args_default_serial(self):
        provider = CLIUUTProvider.from_cli_args(uut_serial=None, uut_serials=None)
        assert provider.get_uut(0).serial == "UUT001"


class TestEnvironmentUUTProvider:
    """EnvironmentUUTProvider resolves UUT from environment variables."""

    def test_global_serial(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL", "ENV_SN001")
        provider = EnvironmentUUTProvider()
        assert provider.get_uut(0).serial == "ENV_SN001"

    def test_site_specific_serial(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL_SITE_0", "SN_A")
        monkeypatch.setenv("LITMUS_UUT_SERIAL_SITE_1", "SN_B")
        provider = EnvironmentUUTProvider()
        assert provider.get_uut(0).serial == "SN_A"
        assert provider.get_uut(1).serial == "SN_B"

    def test_site_specific_overrides_global(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL", "GLOBAL")
        monkeypatch.setenv("LITMUS_UUT_SERIAL_SITE_0", "SPECIFIC")
        provider = EnvironmentUUTProvider()
        assert provider.get_uut(0).serial == "SPECIFIC"

    def test_no_serial_raises(self):
        provider = EnvironmentUUTProvider()
        for key in ["LITMUS_UUT_SERIAL", "LITMUS_UUT_SERIAL_SITE_0"]:
            os.environ.pop(key, None)
        with pytest.raises(ValueError, match="No UUT serial"):
            provider.get_uut(0)

    def test_metadata_from_env(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL", "SN001")
        monkeypatch.setenv("LITMUS_UUT_PART_NUMBER", "PN-200")
        monkeypatch.setenv("LITMUS_UUT_REVISION", "C")
        monkeypatch.setenv("LITMUS_UUT_LOT_NUMBER", "LOT99")
        provider = EnvironmentUUTProvider()
        uut = provider.get_uut(0)
        assert uut.part_number == "PN-200"
        assert uut.revision == "C"
        assert uut.lot_number == "LOT99"


class TestParseSerials:
    """CLIUUTProvider.parse_serials handles indexed, named, and positional formats."""

    def test_indexed_format(self):
        result = CLIUUTProvider.parse_serials("0=SN001,1=SN002")
        assert result == {0: "SN001", 1: "SN002"}

    def test_positional_format(self):
        sites = [ResolvedSite(site_index=0), ResolvedSite(site_index=1)]
        result = CLIUUTProvider.parse_serials("SN001,SN002", sites=sites)
        assert result == {0: "SN001", 1: "SN002"}

    def test_named_format(self):
        sites = [
            ResolvedSite(site_index=0, site_name="left"),
            ResolvedSite(site_index=1, site_name="right"),
        ]
        result = CLIUUTProvider.parse_serials("left=SN001,right=SN002", sites=sites)
        assert result == {0: "SN001", 1: "SN002"}

    def test_positional_preserves_order(self):
        sites = [
            ResolvedSite(site_index=0),
            ResolvedSite(site_index=1),
            ResolvedSite(site_index=2),
        ]
        result = CLIUUTProvider.parse_serials("A,B,C", sites=sites)
        assert result == {0: "A", 1: "B", 2: "C"}

    def test_indexed_with_spaces(self):
        result = CLIUUTProvider.parse_serials(" 0 = SN001 , 1 = SN002 ")
        assert result == {0: "SN001", 1: "SN002"}

    def test_positional_without_sites_raises(self):
        with pytest.raises(ValueError, match="multi-site fixture"):
            CLIUUTProvider.parse_serials("SN001,SN002")

    def test_positional_count_mismatch_raises(self):
        sites = [ResolvedSite(site_index=0), ResolvedSite(site_index=1), ResolvedSite(site_index=2)]
        with pytest.raises(ValueError, match="2 serial.*3 site"):
            CLIUUTProvider.parse_serials("SN001,SN002", sites=sites)

    def test_mixed_indexed_positional_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            CLIUUTProvider.parse_serials("0=SN001,SN002")


class TestUUTProviderProtocol:
    """UUTProvider is a runtime-checkable protocol."""

    def test_cli_provider_satisfies_protocol(self):
        provider = CLIUUTProvider(serial="SN001")
        assert isinstance(provider, UUTProvider)

    def test_env_provider_satisfies_protocol(self):
        provider = EnvironmentUUTProvider()
        assert isinstance(provider, UUTProvider)

    def test_custom_provider_satisfies_protocol(self):
        class MyProvider:
            def get_uut(self, site_index: int) -> UUT:
                return UUT(serial=f"CUSTOM_{site_index}")

        assert isinstance(MyProvider(), UUTProvider)
