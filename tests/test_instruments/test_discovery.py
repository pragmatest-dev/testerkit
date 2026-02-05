"""Tests for instrument discovery module."""

import pytest

from litmus.instruments.discovery import (
    get_protocol,
    list_protocols,
    parse_idn,
    register_protocol,
)
from litmus.instruments.models import InstrumentInfo


class TestParseIdn:
    """Tests for *IDN? response parsing."""

    def test_standard_idn(self):
        """Parse standard IEEE 488.2 *IDN? format."""
        info = parse_idn("Keithley,2400,ABC123,A02")
        assert info.manufacturer == "Keithley"
        assert info.model == "2400"
        assert info.serial == "ABC123"
        assert info.firmware == "A02"

    def test_idn_with_spaces(self):
        """Handle spaces around fields."""
        info = parse_idn("Keithley , Model 2400 , ABC123 , A02")
        assert info.manufacturer == "Keithley"
        assert info.model == "Model 2400"
        assert info.serial == "ABC123"
        assert info.firmware == "A02"

    def test_partial_idn(self):
        """Handle partial *IDN? response."""
        info = parse_idn("Keithley,2400")
        assert info.manufacturer == "Keithley"
        assert info.model == "2400"
        assert info.serial is None
        assert info.firmware is None

    def test_empty_idn(self):
        """Handle empty *IDN? response."""
        info = parse_idn("")
        assert not info  # Empty info is falsy

    def test_idn_with_empty_fields(self):
        """Handle *IDN? with empty fields."""
        info = parse_idn("Keithley,,ABC123,")
        assert info.manufacturer == "Keithley"
        assert info.model is None  # Empty string becomes None
        assert info.serial == "ABC123"
        assert info.firmware is None

    def test_idn_extra_fields(self):
        """Extra fields beyond standard 4 are ignored."""
        info = parse_idn("Keithley,2400,ABC123,A02,Extra,Fields")
        assert info.manufacturer == "Keithley"
        assert info.model == "2400"
        assert info.serial == "ABC123"
        assert info.firmware == "A02"

    def test_keysight_idn(self):
        """Parse Keysight/Agilent style *IDN?."""
        info = parse_idn("Agilent Technologies,34401A,MY12345678,02.03-01.01-02.02")
        assert info.manufacturer == "Agilent Technologies"
        assert info.model == "34401A"
        assert info.serial == "MY12345678"
        assert info.firmware == "02.03-01.01-02.02"

    def test_rohde_schwarz_idn(self):
        """Parse Rohde & Schwarz style *IDN?."""
        info = parse_idn("Rohde&Schwarz,FSW,1234567890123456,1.2.3.4-5.6.7.8")
        assert info.manufacturer == "Rohde&Schwarz"
        assert info.model == "FSW"

    def test_rigol_idn(self):
        """Parse Rigol style *IDN?."""
        info = parse_idn("RIGOL TECHNOLOGIES,DL3021,DL3A123456789,00.01.00")
        assert info.manufacturer == "RIGOL TECHNOLOGIES"
        assert info.model == "DL3021"


class TestProtocolRegistry:
    """Tests for protocol registry."""

    def test_builtin_protocols_registered(self):
        """Built-in protocols should be registered."""
        protocols = list_protocols()
        assert "visa" in protocols
        assert "ni" in protocols
        assert "serial" in protocols

    def test_get_protocol(self):
        """get_protocol returns functions for registered protocols."""
        handler = get_protocol("visa")
        assert handler is not None
        discover_fn, get_info_fn = handler
        assert callable(discover_fn)
        assert callable(get_info_fn)

    def test_get_protocol_unknown(self):
        """get_protocol returns None for unknown protocol."""
        assert get_protocol("unknown_protocol_xyz") is None

    def test_register_custom_protocol(self):
        """Custom protocols can be registered."""

        def my_discover():
            return ["CUSTOM::1", "CUSTOM::2"]

        def my_get_info(resource):
            return InstrumentInfo(manufacturer="Custom", model=resource)

        register_protocol("custom_test", my_discover, my_get_info)

        # Verify registration
        handler = get_protocol("custom_test")
        assert handler is not None

        discover_fn, get_info_fn = handler
        resources = discover_fn()
        assert resources == ["CUSTOM::1", "CUSTOM::2"]

        info = get_info_fn("CUSTOM::1")
        assert info.manufacturer == "Custom"
        assert info.model == "CUSTOM::1"


class TestVisaDiscovery:
    """Tests for VISA discovery functions.

    These tests don't require actual hardware - they test the interface
    and error handling.
    """

    def test_discover_visa_returns_list(self):
        """discover_visa returns a list (possibly empty without hardware)."""
        from litmus.instruments.discovery import discover_visa

        result = discover_visa()
        assert isinstance(result, list)

    def test_get_info_visa_returns_none_for_invalid(self):
        """get_info_visa returns None for invalid resource."""
        from litmus.instruments.discovery import get_info_visa

        result = get_info_visa("INVALID::RESOURCE::STRING")
        assert result is None


class TestUnifiedDiscovery:
    """Tests for unified discovery interface."""

    def test_discover_returns_dict(self):
        """discover() returns dict mapping protocol to resources."""
        from litmus.instruments.discovery import discover

        result = discover(["visa"])
        assert isinstance(result, dict)
        assert "visa" in result
        assert isinstance(result["visa"], list)

    def test_discover_multiple_protocols(self):
        """discover() can scan multiple protocols."""
        from litmus.instruments.discovery import discover

        result = discover(["visa", "serial"])
        assert "visa" in result
        assert "serial" in result

    def test_discover_and_identify(self):
        """discover_and_identify returns tuples of (resource, info)."""
        from litmus.instruments.discovery import discover_and_identify

        result = discover_and_identify(["visa"])
        assert isinstance(result, dict)
        assert "visa" in result
        # Each item should be (resource, info) tuple
        for resource, info in result["visa"]:
            assert isinstance(resource, str)
            assert info is None or isinstance(info, InstrumentInfo)

    def test_get_info_unknown_protocol(self):
        """get_info returns None for unknown protocol."""
        from litmus.instruments.discovery import get_info

        result = get_info("unknown_protocol_xyz", "some_resource")
        assert result is None
