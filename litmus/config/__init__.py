"""Litmus configuration system - Pydantic models and YAML loading."""

from litmus.config.loader import (
    load_specifications,
    load_station_instance,
    load_station_types,
    load_yaml,
    resolve_all_limit_refs,
    resolve_limit_ref,
)
from litmus.config.models import (
    DialogConfig,
    FixtureChannel,
    FixtureConfig,
    InstrumentConfig,
    InstrumentInstance,
    Limit,
    RetryConfig,
    Specification,
    StationInstance,
    StationType,
    TestSequenceConfig,
    TestStepConfig,
)

__all__ = [
    # Models
    "DialogConfig",
    "FixtureChannel",
    "FixtureConfig",
    "InstrumentConfig",
    "InstrumentInstance",
    "Limit",
    "RetryConfig",
    "Specification",
    "StationInstance",
    "StationType",
    "TestSequenceConfig",
    "TestStepConfig",
    # Loader functions
    "load_specifications",
    "load_station_instance",
    "load_station_types",
    "load_yaml",
    "resolve_all_limit_refs",
    "resolve_limit_ref",
]
