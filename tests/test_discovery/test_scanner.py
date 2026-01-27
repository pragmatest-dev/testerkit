"""Tests for instrument discovery scanner."""

from litmus.discovery import DiscoveredInstrument, InstrumentScanner
from litmus.instruments.simulated import get_sim_resource_manager, get_simulated_resource


class TestInstrumentScanner:
    """Tests for InstrumentScanner using simulated backend."""

    def test_init(self):
        scanner = InstrumentScanner()
        assert scanner.visa_library == ""

    def test_init_with_visa_library(self):
        scanner = InstrumentScanner(visa_library="/path/to/visa.so")
        assert scanner.visa_library == "/path/to/visa.so"

    def test_scan_all_simulated(self):
        visa_lib = get_sim_resource_manager()
        scanner = InstrumentScanner(visa_library=visa_lib)

        instruments = scanner.scan_all()

        assert len(instruments) >= 1
        # Find our simulated DMM - resource names are normalized by pyvisa
        # TCPIP::192.168.1.100::INSTR -> TCPIP0::192.168.1.100::inst0::INSTR
        dmm = next(
            (i for i in instruments if "192.168.1.100" in i.resource),
            None,
        )
        assert dmm is not None
        assert dmm.reachable is True
        assert dmm.manufacturer == "Litmus"
        assert dmm.model == "SimDMM"

    def test_probe_resource_simulated(self):
        visa_lib = get_sim_resource_manager()
        scanner = InstrumentScanner(visa_library=visa_lib)
        resource = get_simulated_resource()

        inst = scanner.probe_resource(resource)

        assert isinstance(inst, DiscoveredInstrument)
        assert inst.resource == resource
        assert inst.reachable is True
        assert inst.idn == "Litmus,SimDMM,SN001,1.0"
        assert inst.manufacturer == "Litmus"
        assert inst.model == "SimDMM"
        assert inst.serial == "SN001"
        assert inst.firmware == "1.0"

    def test_probe_resource_unreachable(self):
        # Use default VISA library which won't have our simulated resource
        scanner = InstrumentScanner()

        # Probe a non-existent resource
        inst = scanner.probe_resource("TCPIP::192.168.255.255::INSTR")

        assert inst.reachable is False
        assert inst.error is not None
        assert inst.idn is None


class TestIDNParsing:
    """Tests for IDN response parsing."""

    def test_full_idn_parsed(self):
        visa_lib = get_sim_resource_manager()
        scanner = InstrumentScanner(visa_library=visa_lib)
        resource = get_simulated_resource()

        inst = scanner.probe_resource(resource)

        # IDN: "Litmus,SimDMM,SN001,1.0"
        assert inst.manufacturer == "Litmus"
        assert inst.model == "SimDMM"
        assert inst.serial == "SN001"
        assert inst.firmware == "1.0"
