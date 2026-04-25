"""Instrument driver stubs.

Each class defines the interface your real driver would fulfill. When
running with ``--mock-instruments`` the plugin wraps each class in a
``Mock`` that returns the values declared in the station YAML's
``mock_config`` block. Swap these for your PyVISA / PyMeasure drivers
without touching the test code.
"""

from drivers.dmm import DMM
from drivers.psu import PSU

__all__ = ["DMM", "PSU"]
