"""Tests for instrument discovery functions."""

from litmus.instruments.discovery import (
    DiscoveryProtocol,
    get_protocol,
    list_protocols,
    parse_idn,
)
from litmus.instruments.models import InstrumentInfo


class TestParseIDN:
    """Tests for IDN response parsing."""

    def test_full_idn(self):
        info = parse_idn("Keysight,34465A,MY12345678,A.02.14")
        assert info.manufacturer == "Keysight"
        assert info.model == "34465A"
        assert info.serial == "MY12345678"
        assert info.firmware == "A.02.14"

    def test_empty_string(self):
        info = parse_idn("")
        assert not info

    def test_partial_idn(self):
        info = parse_idn("Rigol,DS1054Z")
        assert info.manufacturer == "Rigol"
        assert info.model == "DS1054Z"
        assert info.serial is None
        assert info.firmware is None

    def test_extra_whitespace(self):
        info = parse_idn(" Keysight , 34461A , SN001 , 1.0 ")
        assert info.manufacturer == "Keysight"
        assert info.model == "34461A"
        assert info.serial == "SN001"
        assert info.firmware == "1.0"


class TestProtocolRegistry:
    """Tests for pluggable protocol registry."""

    def test_builtin_protocols_registered(self):
        protocols = list_protocols()
        assert "visa" in protocols
        assert "ni" in protocols
        assert "serial" in protocols

    def test_get_registered_protocol(self):
        handler = get_protocol("visa")
        assert handler is not None
        assert callable(handler.discover)
        assert callable(handler.get_info)

    def test_get_unregistered_protocol(self):
        assert get_protocol("nonexistent") is None

    def test_register_custom_protocol(self):
        class ScannerCustom(DiscoveryProtocol):
            name = "scanner_test_custom"

            def discover(self) -> list[str]:
                return ["CUSTOM::1"]

            def get_info(self, resource: str) -> InstrumentInfo | None:
                return InstrumentInfo(manufacturer="Custom")

        assert "scanner_test_custom" in list_protocols()

        handler = get_protocol("scanner_test_custom")
        assert handler is not None
        assert handler.discover() == ["CUSTOM::1"]
        info = handler.get_info("CUSTOM::1")
        assert info is not None
        assert info.manufacturer == "Custom"
