"""Tests for UUT identity providers."""

import os

import pytest

from litmus.data.models import UUT
from litmus.execution.uut_provider import (
    CLIUUTProvider,
    EnvironmentUUTProvider,
    UUTProvider,
)


class TestCLIUUTProvider:
    """CLIUUTProvider resolves UUT from CLI-style arguments."""

    def test_single_serial_returns_same_for_all_slots(self):
        provider = CLIUUTProvider(serial="SN001")
        uut1 = provider.get_uut("slot_1")
        uut2 = provider.get_uut("slot_2")
        assert uut1.serial == "SN001"
        assert uut2.serial == "SN001"

    def test_per_slot_serials(self):
        provider = CLIUUTProvider(serials={"slot_1": "SN001", "slot_2": "SN002"})
        assert provider.get_uut("slot_1").serial == "SN001"
        assert provider.get_uut("slot_2").serial == "SN002"

    def test_per_slot_missing_slot_raises(self):
        provider = CLIUUTProvider(serials={"slot_1": "SN001"})
        with pytest.raises(ValueError, match="No UUT serial for slot 'slot_3'"):
            provider.get_uut("slot_3")

    def test_both_serial_and_serials_raises(self):
        with pytest.raises(ValueError, match="not both"):
            CLIUUTProvider(serial="SN001", serials={"slot_1": "SN002"})

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
        uut = provider.get_uut("any_slot")
        assert uut.part_number == "PN-100"
        assert uut.revision == "B"
        assert uut.lot_number == "LOT42"

    def test_from_cli_args_single(self):
        provider = CLIUUTProvider.from_cli_args(uut_serial="SN999", uut_serials=None)
        assert provider.get_uut("slot_1").serial == "SN999"

    def test_from_cli_args_multi(self):
        provider = CLIUUTProvider.from_cli_args(
            uut_serial=None,
            uut_serials="slot_1=SN001,slot_2=SN002",
        )
        assert provider.get_uut("slot_1").serial == "SN001"
        assert provider.get_uut("slot_2").serial == "SN002"

    def test_from_cli_args_positional(self):
        provider = CLIUUTProvider.from_cli_args(
            uut_serial=None,
            uut_serials="SN001,SN002",
            slot_ids=["slot_1", "slot_2"],
        )
        assert provider.get_uut("slot_1").serial == "SN001"
        assert provider.get_uut("slot_2").serial == "SN002"

    def test_from_cli_args_positional_without_slots_raises(self):
        with pytest.raises(ValueError, match="requires a multi-slot fixture"):
            CLIUUTProvider.from_cli_args(uut_serial=None, uut_serials="SN001,SN002")

    def test_from_cli_args_positional_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="2 serial.*3 slot"):
            CLIUUTProvider.from_cli_args(
                uut_serial=None,
                uut_serials="SN001,SN002",
                slot_ids=["slot_1", "slot_2", "slot_3"],
            )

    def test_from_cli_args_bad_named_format_raises(self):
        with pytest.raises(ValueError, match="Invalid --uut-serials format"):
            CLIUUTProvider.from_cli_args(
                uut_serial=None,
                uut_serials="slot_1=SN001,SN002",  # mixed named/positional
            )

    def test_from_cli_args_default_serial(self):
        provider = CLIUUTProvider.from_cli_args(uut_serial=None, uut_serials=None)
        assert provider.get_uut("slot_1").serial == "UUT001"


class TestEnvironmentUUTProvider:
    """EnvironmentUUTProvider resolves UUT from environment variables."""

    def test_global_serial(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL", "ENV_SN001")
        provider = EnvironmentUUTProvider()
        assert provider.get_uut("slot_1").serial == "ENV_SN001"

    def test_slot_specific_serial(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL_SLOT_1", "SN_A")
        monkeypatch.setenv("LITMUS_UUT_SERIAL_SLOT_2", "SN_B")
        provider = EnvironmentUUTProvider()
        assert provider.get_uut("slot_1").serial == "SN_A"
        assert provider.get_uut("slot_2").serial == "SN_B"

    def test_slot_specific_overrides_global(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL", "GLOBAL")
        monkeypatch.setenv("LITMUS_UUT_SERIAL_SLOT_1", "SPECIFIC")
        provider = EnvironmentUUTProvider()
        assert provider.get_uut("slot_1").serial == "SPECIFIC"

    def test_no_serial_raises(self):
        # Ensure vars are not set (monkeypatch not needed if not set)
        provider = EnvironmentUUTProvider()
        # Remove vars if they happen to exist
        for key in ["LITMUS_UUT_SERIAL", "LITMUS_UUT_SERIAL_SLOT_1"]:
            os.environ.pop(key, None)
        with pytest.raises(ValueError, match="No UUT serial"):
            provider.get_uut("slot_1")

    def test_metadata_from_env(self, monkeypatch):
        monkeypatch.setenv("LITMUS_UUT_SERIAL", "SN001")
        monkeypatch.setenv("LITMUS_UUT_PART_NUMBER", "PN-200")
        monkeypatch.setenv("LITMUS_UUT_REVISION", "C")
        monkeypatch.setenv("LITMUS_UUT_LOT_NUMBER", "LOT99")
        provider = EnvironmentUUTProvider()
        uut = provider.get_uut("any")
        assert uut.part_number == "PN-200"
        assert uut.revision == "C"
        assert uut.lot_number == "LOT99"


class TestParseSerials:
    """CLIUUTProvider.parse_serials handles named and positional formats."""

    def test_named_format(self):
        result = CLIUUTProvider.parse_serials("slot_1=SN001,slot_2=SN002")
        assert result == {"slot_1": "SN001", "slot_2": "SN002"}

    def test_positional_format(self):
        result = CLIUUTProvider.parse_serials("SN001,SN002", slot_ids=["slot_1", "slot_2"])
        assert result == {"slot_1": "SN001", "slot_2": "SN002"}

    def test_positional_preserves_order(self):
        result = CLIUUTProvider.parse_serials("A,B,C", slot_ids=["x", "y", "z"])
        assert list(result.keys()) == ["x", "y", "z"]
        assert list(result.values()) == ["A", "B", "C"]

    def test_named_with_spaces(self):
        result = CLIUUTProvider.parse_serials(" slot_1 = SN001 , slot_2 = SN002 ")
        assert result == {"slot_1": "SN001", "slot_2": "SN002"}

    def test_positional_without_slot_ids_raises(self):
        with pytest.raises(ValueError, match="requires a multi-slot fixture"):
            CLIUUTProvider.parse_serials("SN001,SN002")

    def test_positional_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="2 serial.*3 slot"):
            CLIUUTProvider.parse_serials("SN001,SN002", slot_ids=["a", "b", "c"])

    def test_mixed_named_positional_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            CLIUUTProvider.parse_serials("slot_1=SN001,SN002")


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
            def get_uut(self, slot_id: str) -> UUT:
                return UUT(serial=f"CUSTOM_{slot_id}")

        assert isinstance(MyProvider(), UUTProvider)
