"""Product specification models.

This module provides models for defining product characteristics and test
requirements in a way that shares vocabulary with instrument capabilities.
"""

from litmus.products.context import SpecContext
from litmus.products.limits import derive_limit, derive_limits_for_requirement
from litmus.products.loader import load_product, load_products_from_directory
from litmus.products.models import (
    Characteristic,
    ConditionPoint,
    Product,
    TestRequirement,
)

__all__ = [
    "Characteristic",
    "ConditionPoint",
    "Product",
    "SpecContext",
    "TestRequirement",
    "derive_limit",
    "derive_limits_for_requirement",
    "load_product",
    "load_products_from_directory",
]
