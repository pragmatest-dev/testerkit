"""Tests for LXI discovery protocol."""

from unittest.mock import MagicMock, patch

from litmus.instruments.lxi import (
    _parse_identification_xml,
    _parse_resource,
    get_info_lxi,
)

SAMPLE_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<LXIDevice>
  <Manufacturer>Keysight Technologies</Manufacturer>
  <Model>34461A</Model>
  <SerialNumber>MY12345678</SerialNumber>
  <FirmwareRevision>A.02.14</FirmwareRevision>
</LXIDevice>
"""


def test_parse_lxi_identification_xml():
    info = _parse_identification_xml(SAMPLE_XML)
    assert info is not None
    assert info.manufacturer == "Keysight Technologies"
    assert info.model == "34461A"
    assert info.serial == "MY12345678"
    assert info.firmware == "A.02.14"


def test_parse_identification_xml_with_namespace():
    xml = b"""\
    <LXIDevice xmlns="http://www.lxistandard.org">
      <Manufacturer>Rigol</Manufacturer>
      <Model>DS1054Z</Model>
      <SerialNumber>SN001</SerialNumber>
      <FirmwareRevision>00.04.04</FirmwareRevision>
    </LXIDevice>
    """
    info = _parse_identification_xml(xml)
    assert info is not None
    assert info.manufacturer == "Rigol"
    assert info.model == "DS1054Z"


def test_parse_identification_xml_bad_xml():
    assert _parse_identification_xml(b"not xml") is None


def test_parse_resource_valid():
    ip, port = _parse_resource("LXI::192.168.1.100:80")
    assert ip == "192.168.1.100"
    assert port == 80


def test_parse_resource_invalid():
    import pytest

    with pytest.raises(ValueError):
        _parse_resource("GPIB::16::INSTR")
    with pytest.raises(ValueError):
        _parse_resource("LXI::noport")


def test_get_info_lxi_bad_resource():
    assert get_info_lxi("BAD::resource") is None


@patch("litmus.instruments.lxi.urlopen")
def test_get_info_lxi_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = SAMPLE_XML
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    info = get_info_lxi("LXI::192.168.1.100:80")
    assert info is not None
    assert info.model == "34461A"
    mock_urlopen.assert_called_once()


def test_discover_lxi_no_zeroconf():
    """When zeroconf is not installed, discover_lxi raises ImportError."""
    import pytest

    with patch.dict("sys.modules", {"zeroconf": None}):
        from litmus.instruments.lxi import discover_lxi

        with pytest.raises(ImportError, match="zeroconf"):
            discover_lxi()


def test_lxi_protocol_registered():
    from litmus.instruments.discovery import list_protocols

    assert "lxi" in list_protocols()
