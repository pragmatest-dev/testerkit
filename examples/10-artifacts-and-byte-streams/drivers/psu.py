"""Self-simulating PSU — concrete driver, no Mock infrastructure."""

from __future__ import annotations

import random


class PSU:
    """Self-simulating PSU."""

    def __init__(self, resource: str = "") -> None:
        self.resource = resource
        self._voltage = 0.0

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def set_voltage(self, volts: float) -> None:
        self._voltage = volts

    def measure_voltage(self) -> float:
        """Readback within ±10 mV of commanded."""
        return self._voltage + random.uniform(-0.01, 0.01)

    def measure_current(self) -> float:
        """Idle current roughly proportional to voltage."""
        return self._voltage * 0.012 + random.uniform(-0.0005, 0.0005)
