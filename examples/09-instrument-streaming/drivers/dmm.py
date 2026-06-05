"""Self-simulating DMM driver — no real bench needed.

A real DMM driver opens a VISA session in ``connect()`` and reads
hardware in ``measure_voltage()``. This one fakes both: ``connect()``
is a no-op, and ``measure_voltage()`` returns a 30-second sine wave
(±50 mV) around 3.3 V with ±5 mV per-sample noise. Flat line in the
operator UI = wiring broken.

Self-simulating drivers let the example focus on the streaming
primitives (``channels.stream``) without dragging in the ``Mock(...)``
infrastructure. Swap this class for a real PyMeasure / PyVISA
implementation when you have a bench attached and the script keeps
working unchanged.
"""

from __future__ import annotations

import math
import random
import time

_T0 = time.monotonic()
_PERIOD_S = 30.0  # one full sine cycle every 30 s
_AMPLITUDE_V = 0.05  # ±50 mV swing
_NOISE_V = 0.005  # ±5 mV per-sample noise
_NOMINAL_V = 3.3


class DMM:
    """Self-simulating DMM. Concrete implementation; no Mock needed."""

    def __init__(self, resource: str = "") -> None:
        self.resource = resource

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def measure_voltage(self) -> float:
        """Return one DC voltage reading (simulated drift + noise)."""
        t = time.monotonic() - _T0
        drift = _AMPLITUDE_V * math.sin(2 * math.pi * t / _PERIOD_S)
        noise = random.gauss(0, _NOISE_V)
        return _NOMINAL_V + drift + noise
