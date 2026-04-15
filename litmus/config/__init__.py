"""Litmus configuration system - Pydantic models and YAML loading."""

from litmus.config.enum_meta import (
    LookupResult,
    lookup_enum,
    render_enum_reference,
)
from litmus.config.enums import InstrumentConfig, InstrumentInstance, StationInstance, StationType
from litmus.config.loader import (
    find_test_config,
    get_test_config,
    load_test_config,
)
from litmus.config.test_config import (
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

__all__ = [
    # Models
    "FixtureConfig",
    "FixturePoint",
    "InstrumentConfig",
    "InstrumentInstance",
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
    "StationInstance",
    "StationType",
    "TestConfig",
    "TestSequenceConfig",
    "TestStepConfig",
    "VectorConfig",
    # Enum metadata
    "LookupResult",
    "lookup_enum",
    "render_enum_reference",
    # Loader functions
    "find_test_config",
    "get_test_config",
    "load_test_config",
]
