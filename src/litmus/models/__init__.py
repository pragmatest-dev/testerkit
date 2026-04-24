"""Pure Pydantic types for Litmus domain entities.

This package contains the domain data models — what a product, station,
capability, instrument, or catalog entry *is*. It intentionally has no
behavior, no I/O, and no runtime dependencies on other Litmus packages
that perform I/O or execute tests. Anything that does work (loading YAML,
running tests, rendering UI, serving HTTP) imports *from* this package and
is kept out of it.

This separation exists so that any module can import a domain type without
triggering heavier packages (``products``, ``instruments``, ``execution``,
…) and the indirect dependency cycles they can create.

The re-exports below cover the types most commonly imported by users and
tests. Submodule imports like ``from litmus.models.product import Product``
continue to work — this package guarantees stability of both forms.
"""

from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.config import (
    FixtureConfig,
    Limit,
    MeasurementLimitConfig,
    RetryConfig,
    Specification,
    TestConfig,
    TestSequenceConfig,
    TestStepConfig,
    VectorConfig,
)
from litmus.models.instrument import (
    CalibrationInfo,
    ChannelKind,
    InstrumentInfo,
    InstrumentRecord,
)
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.product import (
    BusSignal,
    Pin,
    PinRole,
    Product,
    ProductCharacteristic,
    SignalGroup,
)
from litmus.models.product_manifest import (
    FileReferences,
    ProductManifest,
    WorkflowStep,
)
from litmus.models.project import OutputConfig, ProjectConfig
from litmus.models.station import StationConfig, StationInstrumentConfig

__all__ = [
    # Catalog
    "InstrumentCatalogEntry",
    # Config / test execution
    "FixtureConfig",
    "Limit",
    "MeasurementLimitConfig",
    "RetryConfig",
    "Specification",
    "TestConfig",
    "TestSequenceConfig",
    "TestStepConfig",
    "VectorConfig",
    # Instrument identity
    "CalibrationInfo",
    "ChannelKind",
    "InstrumentAssetFile",
    "InstrumentInfo",
    "InstrumentRecord",
    # Product
    "BusSignal",
    "FileReferences",
    "Pin",
    "PinRole",
    "Product",
    "ProductCharacteristic",
    "SignalGroup",
    "ProductManifest",
    "WorkflowStep",
    # Project-level
    "OutputConfig",
    "ProjectConfig",
    # Station
    "StationConfig",
    "StationInstrumentConfig",
]
