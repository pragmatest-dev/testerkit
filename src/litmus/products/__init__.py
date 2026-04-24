"""Product specification models.

This module provides models for defining product characteristics and test
requirements in a way that shares vocabulary with instrument capabilities.
"""

from litmus.models.product import (
    Pin,
    PinRole,
    Product,
    ProductCharacteristic,
)
from litmus.models.product_manifest import (
    WORKFLOW_STEP_ORDER,
    FileReferences,
    ProductManifest,
    WorkflowStep,
)
from litmus.products.context import SpecContext
from litmus.products.folder import ProductFolder

__all__ = [
    "FileReferences",
    "Pin",
    "PinRole",
    "Product",
    "ProductCharacteristic",
    "ProductFolder",
    "ProductManifest",
    "SpecContext",
    "WORKFLOW_STEP_ORDER",
    "WorkflowStep",
]
