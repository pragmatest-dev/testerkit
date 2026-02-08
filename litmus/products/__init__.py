"""Product specification models.

This module provides models for defining product characteristics and test
requirements in a way that shares vocabulary with instrument capabilities.
"""

from litmus.products.context import SpecContext
from litmus.products.folder import ProductFolder
from litmus.products.loader import load_product, load_products_from_directory
from litmus.products.manifest import (
    WORKFLOW_STEP_ORDER,
    FileReferences,
    ProductManifest,
    WorkflowStep,
)
from litmus.products.models import (
    Characteristic,
    Product,
)

__all__ = [
    "Characteristic",
    "FileReferences",
    "Product",
    "ProductFolder",
    "ProductManifest",
    "SpecContext",
    "WORKFLOW_STEP_ORDER",
    "WorkflowStep",
    "load_product",
    "load_products_from_directory",
]
