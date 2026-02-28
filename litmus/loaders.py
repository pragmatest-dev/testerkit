"""Centralized validated loaders for all Litmus YAML config files.

Every consumer should call these loaders instead of raw yaml.safe_load.
Each function: yaml.safe_load → Model.model_validate(data) → return typed model.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from litmus.schemas import (
    FixtureFile,
    InstrumentAssetFile,
    ProjectFile,
    SequenceFile,
    StationFile,
)


def load_station(path: Path) -> StationFile:
    """Load and validate a station YAML file.

    Args:
        path: Path to station YAML file.

    Returns:
        Validated StationFile model.

    Raises:
        pydantic.ValidationError: If data doesn't match schema.
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return StationFile.model_validate(data)


def load_sequence(path: Path) -> SequenceFile:
    """Load and validate a sequence YAML file.

    Args:
        path: Path to sequence YAML file.

    Returns:
        Validated SequenceFile model.

    Raises:
        pydantic.ValidationError: If data doesn't match schema.
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return SequenceFile.model_validate(data)


def load_fixture(path: Path) -> FixtureFile:
    """Load and validate a fixture YAML file.

    Args:
        path: Path to fixture YAML file.

    Returns:
        Validated FixtureFile model.

    Raises:
        pydantic.ValidationError: If data doesn't match schema.
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return FixtureFile.model_validate(data)


def load_instrument_asset(path: Path) -> InstrumentAssetFile:
    """Load and validate an instrument asset YAML file.

    Args:
        path: Path to instrument asset YAML file (id, protocol, info, calibration).

    Returns:
        Validated InstrumentAssetFile model.

    Raises:
        pydantic.ValidationError: If data doesn't match schema.
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return InstrumentAssetFile.model_validate(data)


def load_project(path: Path) -> ProjectFile:
    """Load and validate a litmus.yaml project config file.

    Args:
        path: Path to litmus.yaml.

    Returns:
        Validated ProjectFile model.

    Raises:
        pydantic.ValidationError: If data doesn't match schema.
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return ProjectFile.model_validate(data)
