"""YAML loading and configuration resolution for Litmus."""

from pathlib import Path
from typing import Any

import yaml

from litmus.config.models import (
    Limit,
    RetryConfig,
)


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

        # Parse test-level mocks (new) or _mock (legacy)
        if "mocks" in test_data:
            config["mocks"] = test_data["mocks"]
        elif "_mock" in test_data:
            config["mocks"] = test_data["_mock"]

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
