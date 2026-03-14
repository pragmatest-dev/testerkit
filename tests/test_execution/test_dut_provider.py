"""Tests for DUT identity providers."""

import os

import pytest

from litmus.data.models import DUT
from litmus.execution.dut_provider import (
    CLIDUTProvider,
    DUTProvider,
    EnvironmentDUTProvider,
)


class TestCLIDUTProvider:
    """CLIDUTProvider resolves DUT from CLI-style arguments."""

    def test_single_serial_returns_same_for_all_slots(self):
        provider = CLIDUTProvider(serial="SN001")
        dut1 = provider.get_dut("slot_1")
        dut2 = provider.get_dut("slot_2")
        assert dut1.serial == "SN001"
        assert dut2.serial == "SN001"

    def test_per_slot_serials(self):
        provider = CLIDUTProvider(serials={"slot_1": "SN001", "slot_2": "SN002"})
        assert provider.get_dut("slot_1").serial == "SN001"
        assert provider.get_dut("slot_2").serial == "SN002"

    def test_per_slot_missing_slot_raises(self):
        provider = CLIDUTProvider(serials={"slot_1": "SN001"})
        with pytest.raises(ValueError, match="No DUT serial for slot 'slot_3'"):
            provider.get_dut("slot_3")

    def test_both_serial_and_serials_raises(self):
        with pytest.raises(ValueError, match="not both"):
            CLIDUTProvider(serial="SN001", serials={"slot_1": "SN002"})

    def test_neither_serial_nor_serials_raises(self):
        with pytest.raises(ValueError, match="must be provided"):
            CLIDUTProvider()

    def test_metadata_fields_passed_through(self):
        provider = CLIDUTProvider(
            serial="SN001",
            part_number="PN-100",
            revision="B",
            lot_number="LOT42",
        )
        dut = provider.get_dut("any_slot")
        assert dut.part_number == "PN-100"
        assert dut.revision == "B"
        assert dut.lot_number == "LOT42"

    def test_from_cli_args_single(self):
        provider = CLIDUTProvider.from_cli_args(
            dut_serial="SN999", dut_serials=None
        )
        assert provider.get_dut("slot_1").serial == "SN999"

    def test_from_cli_args_multi(self):
        provider = CLIDUTProvider.from_cli_args(
            dut_serial=None,
            dut_serials="slot_1=SN001,slot_2=SN002",
        )
        assert provider.get_dut("slot_1").serial == "SN001"
        assert provider.get_dut("slot_2").serial == "SN002"

    def test_from_cli_args_positional(self):
        provider = CLIDUTProvider.from_cli_args(
            dut_serial=None,
            dut_serials="SN001,SN002",
            slot_ids=["slot_1", "slot_2"],
        )
        assert provider.get_dut("slot_1").serial == "SN001"
        assert provider.get_dut("slot_2").serial == "SN002"

    def test_from_cli_args_positional_without_slots_raises(self):
        with pytest.raises(ValueError, match="requires a multi-slot fixture"):
            CLIDUTProvider.from_cli_args(
                dut_serial=None, dut_serials="SN001,SN002"
            )

    def test_from_cli_args_positional_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="2 serial.*3 slot"):
            CLIDUTProvider.from_cli_args(
                dut_serial=None,
                dut_serials="SN001,SN002",
                slot_ids=["slot_1", "slot_2", "slot_3"],
            )

    def test_from_cli_args_bad_named_format_raises(self):
        with pytest.raises(ValueError, match="Invalid --dut-serials format"):
            CLIDUTProvider.from_cli_args(
                dut_serial=None,
                dut_serials="slot_1=SN001,SN002",  # mixed named/positional
            )

    def test_from_cli_args_default_serial(self):
        provider = CLIDUTProvider.from_cli_args(dut_serial=None, dut_serials=None)
        assert provider.get_dut("slot_1").serial == "DUT001"


class TestEnvironmentDUTProvider:
    """EnvironmentDUTProvider resolves DUT from environment variables."""

    def test_global_serial(self, monkeypatch):
        monkeypatch.setenv("LITMUS_DUT_SERIAL", "ENV_SN001")
        provider = EnvironmentDUTProvider()
        assert provider.get_dut("slot_1").serial == "ENV_SN001"

    def test_slot_specific_serial(self, monkeypatch):
        monkeypatch.setenv("LITMUS_DUT_SERIAL_SLOT_1", "SN_A")
        monkeypatch.setenv("LITMUS_DUT_SERIAL_SLOT_2", "SN_B")
        provider = EnvironmentDUTProvider()
        assert provider.get_dut("slot_1").serial == "SN_A"
        assert provider.get_dut("slot_2").serial == "SN_B"

    def test_slot_specific_overrides_global(self, monkeypatch):
        monkeypatch.setenv("LITMUS_DUT_SERIAL", "GLOBAL")
        monkeypatch.setenv("LITMUS_DUT_SERIAL_SLOT_1", "SPECIFIC")
        provider = EnvironmentDUTProvider()
        assert provider.get_dut("slot_1").serial == "SPECIFIC"

    def test_no_serial_raises(self):
        # Ensure vars are not set (monkeypatch not needed if not set)
        provider = EnvironmentDUTProvider()
        # Remove vars if they happen to exist
        for key in ["LITMUS_DUT_SERIAL", "LITMUS_DUT_SERIAL_SLOT_1"]:
            os.environ.pop(key, None)
        with pytest.raises(ValueError, match="No DUT serial"):
            provider.get_dut("slot_1")

    def test_metadata_from_env(self, monkeypatch):
        monkeypatch.setenv("LITMUS_DUT_SERIAL", "SN001")
        monkeypatch.setenv("LITMUS_DUT_PART_NUMBER", "PN-200")
        monkeypatch.setenv("LITMUS_DUT_REVISION", "C")
        monkeypatch.setenv("LITMUS_DUT_LOT_NUMBER", "LOT99")
        provider = EnvironmentDUTProvider()
        dut = provider.get_dut("any")
        assert dut.part_number == "PN-200"
        assert dut.revision == "C"
        assert dut.lot_number == "LOT99"


class TestParseSerials:
    """CLIDUTProvider.parse_serials handles named and positional formats."""

    def test_named_format(self):
        result = CLIDUTProvider.parse_serials("slot_1=SN001,slot_2=SN002")
        assert result == {"slot_1": "SN001", "slot_2": "SN002"}

    def test_positional_format(self):
        result = CLIDUTProvider.parse_serials(
            "SN001,SN002", slot_ids=["slot_1", "slot_2"]
        )
        assert result == {"slot_1": "SN001", "slot_2": "SN002"}

    def test_positional_preserves_order(self):
        result = CLIDUTProvider.parse_serials(
            "A,B,C", slot_ids=["x", "y", "z"]
        )
        assert list(result.keys()) == ["x", "y", "z"]
        assert list(result.values()) == ["A", "B", "C"]

    def test_named_with_spaces(self):
        result = CLIDUTProvider.parse_serials(
            " slot_1 = SN001 , slot_2 = SN002 "
        )
        assert result == {"slot_1": "SN001", "slot_2": "SN002"}

    def test_positional_without_slot_ids_raises(self):
        with pytest.raises(ValueError, match="requires a multi-slot fixture"):
            CLIDUTProvider.parse_serials("SN001,SN002")

    def test_positional_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="2 serial.*3 slot"):
            CLIDUTProvider.parse_serials(
                "SN001,SN002", slot_ids=["a", "b", "c"]
            )

    def test_mixed_named_positional_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            CLIDUTProvider.parse_serials("slot_1=SN001,SN002")


class TestDUTProviderProtocol:
    """DUTProvider is a runtime-checkable protocol."""

    def test_cli_provider_satisfies_protocol(self):
        provider = CLIDUTProvider(serial="SN001")
        assert isinstance(provider, DUTProvider)

    def test_env_provider_satisfies_protocol(self):
        provider = EnvironmentDUTProvider()
        assert isinstance(provider, DUTProvider)

    def test_custom_provider_satisfies_protocol(self):
        class MyProvider:
            def get_dut(self, slot_id: str) -> DUT:
                return DUT(serial=f"CUSTOM_{slot_id}")

        assert isinstance(MyProvider(), DUTProvider)
