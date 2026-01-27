"""Tests for discovery models."""

from litmus.discovery import DiscoveredInstrument


class TestDiscoveredInstrument:
    """Tests for DiscoveredInstrument model."""

    def test_minimal(self):
        inst = DiscoveredInstrument(resource="TCPIP::192.168.1.100::INSTR")
        assert inst.resource == "TCPIP::192.168.1.100::INSTR"
        assert inst.reachable is True
        assert inst.idn is None
        assert inst.error is None

    def test_fully_populated(self):
        inst = DiscoveredInstrument(
            resource="TCPIP::192.168.1.100::INSTR",
            idn="Keysight,34465A,MY12345678,A.02.14-02.40-02.14-00.49-04-01",
            manufacturer="Keysight",
            model="34465A",
            serial="MY12345678",
            firmware="A.02.14-02.40-02.14-00.49-04-01",
            reachable=True,
        )
        assert inst.manufacturer == "Keysight"
        assert inst.model == "34465A"
        assert inst.serial == "MY12345678"

    def test_unreachable_with_error(self):
        inst = DiscoveredInstrument(
            resource="GPIB::5::INSTR",
            reachable=False,
            error="Timeout: device not responding",
        )
        assert inst.reachable is False
        assert inst.error == "Timeout: device not responding"
        assert inst.idn is None

    def test_serialization(self):
        inst = DiscoveredInstrument(
            resource="USB::0x1234::0x5678::INSTR",
            idn="Vendor,Model,SN123,1.0",
            manufacturer="Vendor",
            model="Model",
        )
        data = inst.model_dump()
        assert data["resource"] == "USB::0x1234::0x5678::INSTR"
        assert data["manufacturer"] == "Vendor"
        assert data["model"] == "Model"
