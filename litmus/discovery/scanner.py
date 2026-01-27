"""VISA instrument discovery scanner."""

import pyvisa

from litmus.discovery.models import DiscoveredInstrument


class InstrumentScanner:
    """Scan for and probe VISA instruments.

    Enumerates available VISA resources and probes each one
    to retrieve identification information.
    """

    def __init__(self, visa_library: str = ""):
        """Initialize scanner.

        Args:
            visa_library: Path to VISA library or pyvisa-sim config
        """
        self.visa_library = visa_library

    def scan_all(self) -> list[DiscoveredInstrument]:
        """Find and probe all VISA resources.

        Returns:
            List of DiscoveredInstrument objects for each found resource
        """
        rm = pyvisa.ResourceManager(self.visa_library)
        resources = rm.list_resources()
        rm.close()

        return [self.probe_resource(r) for r in resources]

    def probe_resource(self, resource: str) -> DiscoveredInstrument:
        """Probe a specific VISA resource.

        Attempts to connect and query *IDN? to identify the instrument.

        Args:
            resource: VISA resource string to probe

        Returns:
            DiscoveredInstrument with identification info or error
        """
        try:
            rm = pyvisa.ResourceManager(self.visa_library)
            inst = rm.open_resource(resource)
            inst.timeout = 2000
            # Set standard SCPI message terminators
            inst.write_termination = "\n"
            inst.read_termination = "\n"

            idn = inst.query("*IDN?").strip()
            inst.close()
            rm.close()

            # Parse IDN: manufacturer,model,serial,firmware
            parts = idn.split(",")
            return DiscoveredInstrument(
                resource=resource,
                idn=idn,
                manufacturer=parts[0].strip() if len(parts) > 0 else None,
                model=parts[1].strip() if len(parts) > 1 else None,
                serial=parts[2].strip() if len(parts) > 2 else None,
                firmware=parts[3].strip() if len(parts) > 3 else None,
            )
        except Exception as e:
            return DiscoveredInstrument(
                resource=resource,
                reachable=False,
                error=str(e),
            )
