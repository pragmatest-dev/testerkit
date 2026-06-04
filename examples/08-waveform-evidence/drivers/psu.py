"""Programmable Power Supply (PSU) driver class.

Minimal interface. Tests use ``Mock(PSU, ...)`` to stub return values
without a bench. Real benches subclass or replace this with a vendor driver.
"""


class PSU:
    def __init__(self, resource: str = "") -> None:
        self.resource = resource

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def set_voltage(self, volts: float) -> None:
        """Command the PSU output voltage."""
        raise NotImplementedError

    def measure_voltage(self) -> float:
        """Read back the PSU output voltage."""
        raise NotImplementedError

    def measure_current(self) -> float:
        """Read back the PSU output current."""
        raise NotImplementedError
