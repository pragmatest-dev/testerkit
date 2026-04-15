"""Product specification models.

This module provides models for defining product characteristics and test
requirements in a way that shares vocabulary with instrument capabilities.
"""

from litmus.products.context import SpecContext
from litmus.products.manifest import (
    WORKFLOW_STEP_ORDER,
    FileReferences,
    ProductManifest,
    WorkflowStep,
)
from litmus.products.models import (
    Pin,
    PinRole,
    Product,
    ProductCharacteristic,
)

# ProductFolder is intentionally not re-exported here: it transitively imports
# `litmus.store`, which imports back from `litmus.schemas` → `litmus.products`,
# creating an import cycle that breaks wheel installs. Import it directly from
# `litmus.products.folder` when needed.

__all__ = [
    "FileReferences",
    "Pin",
    "PinRole",
    "Product",
    "ProductCharacteristic",
    "ProductManifest",
    "SpecContext",
    "WORKFLOW_STEP_ORDER",
    "WorkflowStep",
]
