"""YAML loading and configuration resolution for Litmus."""

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from litmus.config.models import (
    InstrumentConfig,
    InstrumentInstance,
    Limit,
    RetryConfig,
    Specification,
    StationInstance,
    StationType,
)

T = TypeVar("T", bound=BaseModel)


def load_yaml(path: Path, model: type[T]) -> T:
    """Load and validate YAML file against a Pydantic model.

    Args:
        path: Path to the YAML file.
        model: Pydantic model class to validate against.

    Returns:
        Validated model instance.

    Raises:
        FileNotFoundError: If the YAML file doesn't exist.
        yaml.YAMLError: If the YAML is malformed.
        pydantic.ValidationError: If the data doesn't match the model.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    return model.model_validate(data)


def load_station_types(path: Path) -> dict[str, StationType]:
    """Load station types from a YAML file (e.g., _base.yaml).

    Expected YAML format:
        station_types:
          universal_bench:
            description: "..."
            instruments:
              dmm:
                type: dmm
                driver: pyvisa
            capabilities: [functional, parametric]

    Args:
        path: Path to the station types YAML file.

    Returns:
        Dictionary mapping station type ID to StationType.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    station_types = {}
    for type_id, type_data in data.get("station_types", {}).items():
        # Parse instruments
        instruments = {}
        for inst_id, inst_data in type_data.get("instruments", {}).items():
            instruments[inst_id] = InstrumentConfig.model_validate(inst_data)

        station_types[type_id] = StationType(
            id=type_id,
            description=type_data.get("description", ""),
            instruments=instruments,
            capabilities=type_data.get("capabilities", []),
        )

    return station_types


def load_station_instance(path: Path) -> StationInstance:
    """Load a station instance configuration.

    Expected YAML format:
        station:
          id: station_001
          station_type: universal_bench
          location: "Lab A, Bench 3"
          instruments:
            dmm:
              type: dmm
              resource: "TCPIP::192.168.1.101::INSTR"
          active_fixture: product_a_fixture

    Args:
        path: Path to the station instance YAML file.

    Returns:
        StationInstance object.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    station_data = data.get("station", data)

    # Parse instruments
    instruments = {}
    for inst_id, inst_data in station_data.get("instruments", {}).items():
        instruments[inst_id] = InstrumentInstance.model_validate(inst_data)

    return StationInstance(
        id=station_data["id"],
        station_type=station_data["station_type"],
        location=station_data.get("location"),
        instruments=instruments,
        active_fixture=station_data.get("active_fixture"),
    )


def load_specifications(path: Path) -> dict[str, Specification]:
    """Load product specifications from YAML.

    Expected YAML format:
        specifications:
          rail_5v:
            id: PWR-RAIL-5V
            description: "5V power rail voltage"
            nominal: 5.0
            tolerance_pct: 5
            units: V

    Args:
        path: Path to the specifications YAML file.

    Returns:
        Dictionary mapping spec key to Specification.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    specifications = {}
    for spec_key, spec_data in data.get("specifications", {}).items():
        # Convert numeric values to float
        spec_dict = dict(spec_data)
        if "nominal" in spec_dict:
            spec_dict["nominal"] = float(spec_dict["nominal"])
        if "tolerance_pct" in spec_dict:
            spec_dict["tolerance_pct"] = float(spec_dict["tolerance_pct"])
        if "tolerance_abs" in spec_dict:
            spec_dict["tolerance_abs"] = float(spec_dict["tolerance_abs"])

        specifications[spec_key] = Specification.model_validate(spec_dict)

    return specifications


def resolve_limit_ref(limit_ref: str, specs: dict[str, dict[str, Specification]]) -> Limit:
    """Resolve a limit reference to a Limit object.

    Limit references follow the pattern: specs.<product>.<spec_key>
    Example: "specs.product_a.rail_5v"

    Args:
        limit_ref: The limit reference string.
        specs: Nested dictionary of specifications by product and key.

    Returns:
        Limit derived from the referenced specification.

    Raises:
        ValueError: If the reference format is invalid or spec not found.
    """
    parts = limit_ref.split(".")
    if len(parts) != 3 or parts[0] != "specs":
        raise ValueError(
            f"Invalid limit reference format: {limit_ref}. Expected: specs.<product>.<spec_key>"
        )

    _, product, spec_key = parts

    if product not in specs:
        raise ValueError(f"Product not found in specs: {product}")
    if spec_key not in specs[product]:
        raise ValueError(f"Specification not found: {spec_key} in product {product}")

    spec = specs[product][spec_key]
    return spec.to_limit()


def resolve_all_limit_refs(
    config: dict[str, Any], specs: dict[str, dict[str, Specification]]
) -> dict[str, Any]:
    """Resolve all limit_ref fields in a configuration dictionary.

    Recursively walks the config and converts limit_ref strings to Limit objects.

    Args:
        config: Configuration dictionary (potentially nested).
        specs: Nested dictionary of specifications by product and key.

    Returns:
        Config with limit_ref fields resolved to Limit objects.
    """
    if isinstance(config, dict):
        result = {}
        for key, value in config.items():
            if key == "limit_ref" and isinstance(value, str):
                result["limit"] = resolve_limit_ref(value, specs)
            elif key == "limit_ref":
                # Skip if already processed or None
                pass
            else:
                result[key] = resolve_all_limit_refs(value, specs)
        return result
    elif isinstance(config, list):
        return [resolve_all_limit_refs(item, specs) for item in config]
    else:
        return config


def load_test_config(path: Path) -> dict[str, dict[str, Any]]:
    """Load test configuration from YAML.

    Expected YAML format:
        test_voltage_sweep:
          vectors:
            expand: product
            voltage: [3.3, 5.0, 12.0]
          limits:
            output_voltage:
              low: 3.0
              high: 3.6
              units: V
          retry:
            max_attempts: 3
            delay_seconds: 0.5

    Args:
        path: Path to the test config YAML file.

    Returns:
        Dictionary mapping test function name to config dict.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}

    configs = {}
    for test_name, test_data in data.items():
        if test_data is None:
            continue

        config: dict[str, Any] = {}

        # Parse vectors
        if "vectors" in test_data:
            config["vectors"] = test_data["vectors"]

        # Parse limits
        if "limits" in test_data:
            limits = {}
            for name, limit_data in test_data["limits"].items():
                if isinstance(limit_data, dict):
                    # Check for callable limit (defer resolution to harness)
                    if "callable" in limit_data:
                        # Keep as dict for harness to resolve at runtime
                        limits[name] = limit_data
                    else:
                        # Convert numeric values to float
                        limit_dict = dict(limit_data)
                        for key in ["low", "high", "nominal"]:
                            if key in limit_dict and limit_dict[key] is not None:
                                limit_dict[key] = float(limit_dict[key])
                        limits[name] = Limit.model_validate(limit_dict)
                else:
                    limits[name] = limit_data
            config["limits"] = limits

        # Parse retry
        if "retry" in test_data:
            config["retry"] = RetryConfig.model_validate(test_data["retry"])

        # Parse test-level _mock (for per-vector mock configuration)
        if "_mock" in test_data:
            config["_mock"] = test_data["_mock"]

        configs[test_name] = config

    return configs


def find_test_config(test_file: Path) -> Path | None:
    """Find config file for a test file.

    Looks for config.yaml in the same directory as the test file.

    Args:
        test_file: Path to the test file.

    Returns:
        Path to config file if found, None otherwise.
    """
    config_path = test_file.parent / "config.yaml"
    if config_path.exists():
        return config_path
    return None


# Cache for loaded test configs
_test_config_cache: dict[Path, dict[str, dict[str, Any]]] = {}


def get_test_config(test_name: str, test_file: Path) -> dict[str, Any] | None:
    """Get config for a specific test function.

    Args:
        test_name: Name of the test function.
        test_file: Path to the test file.

    Returns:
        Config dict for the test, or None if not found.
    """
    config_path = find_test_config(test_file)
    if config_path is None:
        return None

    # Load and cache
    if config_path not in _test_config_cache:
        _test_config_cache[config_path] = load_test_config(config_path)

    return _test_config_cache.get(config_path, {}).get(test_name)
