"""Product driver resolution helpers.

Resolves and loads driver classes for DUT communication handles
(serial port, I2C, debug probe, etc.) using the same import
mechanism as instrument drivers.
"""

from __future__ import annotations

from litmus.instruments.lifecycle import load_driver_class
from litmus.models.product import Product


def resolve_product_driver(product: Product) -> str | None:
    """Return the driver dotted path for a product, or None."""
    return product.driver


def load_product_driver(product: Product) -> type | None:
    """Import and return the driver class for a product.

    Uses the same load_driver_class() as instruments.
    Returns None if product has no driver.
    """
    if not product.driver:
        return None
    return load_driver_class(product.driver)
