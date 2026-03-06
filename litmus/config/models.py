"""Pydantic models for Litmus configuration.

Re-exports from split modules for backwards compatibility.
All imports from litmus.config.models continue to work.
"""

# Enums
# Capability models
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
    InstrumentConfig,
    InstrumentInstance,
    InstrumentType,
    MatchDepth,
    MeasurementFunction,
    ParameterRole,
    StationInstance,
    StationType,
    TerminalRole,
    WaveformShape,
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
    LoopVariableConfig,
    MeasurementLimitConfig,
    PromptConfig,
    RangeConfig,
    RetryConfig,
    Specification,
    TestConfig,
    TestSequenceConfig,
    TestStepConfig,
    VectorConfig,
    ZippedLoopConfig,
)
