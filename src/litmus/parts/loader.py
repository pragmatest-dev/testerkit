"""Part driver resolution helpers.

Resolves and loads driver classes for DUT communication handles
(serial port, I2C, debug probe, etc.) using the same import
mechanism as instrument drivers.
"""

from __future__ import annotations

from litmus.instruments.lifecycle import load_driver_class
from litmus.models.part import Part


def resolve_part_driver(part: Part) -> str | None:
    """Return the driver dotted path for a part, or None."""
    return part.driver


def load_part_driver(part: Part) -> type | None:
    """Import and return the driver class for a part.

    Uses the same load_driver_class() as instruments.
    Returns None if part has no driver.
    """
    if not part.driver:
        return None
    return load_driver_class(part.driver)
