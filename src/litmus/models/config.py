"""Pydantic models for Litmus configuration.

Re-exports capability, enum, and test-config types from ``litmus.config.*``
submodules so callers can import from a single namespace::

    from litmus.models.config import Direction, Capability, TestConfig
"""

__all__ = [
    # Capability models
    "AccuracySpec",
    "Attribute",
    "Capability",
    "ChannelTopology",
    "Condition",
    "ConditionKey",
    "Control",
    "InstrumentCapability",
    "ListSpec",
    "PointSpec",
    "RangeSpec",
    "ResolutionSpec",
    "Signal",
    "SpecBand",
    "SpecQualifier",
    # Enums & constants
    "COAXIAL_CONNECTORS",
    "TRIAX_CONNECTORS",
    "Comparator",
    "CompareMode",
    "ConnectorType",
    "Direction",
    "GroundTopology",
    "InstrumentType",
    "MatchDepth",
    "MeasurementFunction",
    "ParameterRole",
    "TerminalRole",
    "WaveformShape",
    # Station/instrument infrastructure
    "InstrumentConfig",
    "InstrumentInstance",
    "StationInstance",
    "StationType",
    # Test configuration
    "FixtureConfig",
    "FixturePoint",
    "Limit",
    "LimitCallableConfig",
    "LimitExprConfig",
    "LimitLookupConfig",
    "LimitRefConfig",
    "LimitStepConfig",
    "MeasurementLimitConfig",
    "PromptConfig",
    "RangeConfig",
    "RetryConfig",
    "Specification",
    "TestConfig",
    "TestSequenceConfig",
    "TestStepConfig",
    "VectorConfig",
]

from litmus.config.capability import (  # noqa: F401
    AccuracySpec,
    Attribute,
    Capability,
    ChannelTopology,
    Condition,
    ConditionKey,
    Control,
    InstrumentCapability,
    ListSpec,
    PointSpec,
    RangeSpec,
    ResolutionSpec,
    Signal,
    SpecBand,
    SpecQualifier,
)
from litmus.config.enums import (  # noqa: F401
    COAXIAL_CONNECTORS,
    TRIAX_CONNECTORS,
    Comparator,
    CompareMode,
    ConnectorType,
    Direction,
    GroundTopology,
    InstrumentType,
    MatchDepth,
    MeasurementFunction,
    ParameterRole,
    TerminalRole,
    WaveformShape,
)
from litmus.config.station_types import (  # noqa: F401
    InstrumentConfig,
    InstrumentInstance,
    StationInstance,
    StationType,
)

# Test configuration models
from litmus.config.test_config import (  # noqa: F401
    FixtureConfig,
    FixturePoint,
    Limit,
    LimitCallableConfig,
    LimitExprConfig,
    LimitLookupConfig,
    LimitRefConfig,
    LimitStepConfig,
    MeasurementLimitConfig,
    PromptConfig,
    RangeConfig,
    RetryConfig,
    Specification,
    TestConfig,
    TestSequenceConfig,
    TestStepConfig,
    VectorConfig,
)
