"""YAML loading for product specifications."""

from pathlib import Path
from typing import Any

import yaml

from litmus.config.models import Comparator
from litmus.products.models import (
    BusSignal,
    Characteristic,
    ConditionPoint,
    Pin,
    PinType,
    Product,
    SignalGroup,
    TestRequirement,
)
from litmus.utils.loaders import parse_capability_enums


def load_product(path: Path) -> Product:
    """Load a product specification from YAML.

    Expected YAML format:
        product:
          id: power_board_v1
          name: "DC-DC Power Board"
          ...
        characteristics:
          rail_3v3_output:
            direction: output
            domain: voltage
            ...
        test_requirements:
          verify_output_voltage:
            characteristic_ref: rail_3v3_output
            ...

    Args:
        path: Path to the product YAML file.

    Returns:
        Product object with characteristics and test requirements.

    Raises:
        FileNotFoundError: If the YAML file doesn't exist.
        yaml.YAMLError: If the YAML is malformed.
        pydantic.ValidationError: If the data doesn't match the model.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    # Parse product metadata
    product_data = data.get("product", {})
    product_id = product_data.get("id", path.stem)
    product_name = product_data.get("name", product_id)

    # Parse pins
    pins = {}
    for pin_key, pin_data in data.get("pins", {}).items():
        pins[pin_key] = _parse_pin(pin_data)

    # Parse signal groups
    signal_groups = {}
    for group_key, group_data in data.get("signal_groups", {}).items():
        signal_groups[group_key] = _parse_signal_group(group_data)

    # Parse characteristics
    characteristics = {}
    for char_key, char_data in data.get("characteristics", {}).items():
        characteristics[char_key] = _parse_characteristic(char_data)

    # Parse test requirements
    test_requirements = {}
    for req_key, req_data in data.get("test_requirements", {}).items():
        test_requirements[req_key] = _parse_test_requirement(req_data)

    return Product(
        id=product_id,
        name=product_name,
        description=product_data.get("description"),
        revision=product_data.get("revision"),
        datasheet=product_data.get("datasheet"),
        schematic=product_data.get("schematic"),
        pins=pins,
        signal_groups=signal_groups,
        characteristics=characteristics,
        test_requirements=test_requirements,
    )


def _parse_pin(data: dict[str, Any]) -> Pin:
    """Parse a pin from YAML data."""
    pin_type = PinType.SIGNAL
    if "type" in data:
        pin_type = PinType(data["type"].lower())

    return Pin(
        name=data["name"],
        net=data.get("net"),
        type=pin_type,
        description=data.get("description"),
    )


def _parse_signal_group(data: dict[str, Any]) -> SignalGroup:
    """Parse a signal group from YAML data."""
    signals = []
    for sig_data in data.get("signals", []):
        signals.append(
            BusSignal(
                pin=sig_data["pin"],
                role=sig_data["role"],
                index=sig_data.get("index"),
            )
        )

    return SignalGroup(
        protocol=data["protocol"],
        signals=signals,
        parameters=data.get("parameters", {}),
        description=data.get("description"),
    )


def _parse_characteristic(data: dict[str, Any]) -> Characteristic:
    """Parse a characteristic from YAML data."""
    # Parse capability enums (direction, domain, signal_types)
    direction, domain, signal_types = parse_capability_enums(
        data["direction"],
        data["domain"],
        data.get("signal_types", ["dc"]),
    )

    # Parse conditions
    conditions = []
    for cond_data in data.get("conditions", []):
        conditions.append(_parse_condition_point(cond_data))

    return Characteristic(
        direction=direction,
        domain=domain,
        signal_types=signal_types,
        units=data["units"],
        # Physical interface fields
        pin=data.get("pin"),  # Single pin reference
        pins=data.get("pins", []),  # Multiple pins
        net=data.get("net"),  # Schematic net name
        signal_group=data.get("signal_group"),
        channel=data.get("channel"),
        # Traceability
        datasheet_ref=data.get("datasheet_ref"),
        conditions=conditions,
    )


def _parse_condition_point(data: dict[str, Any]) -> ConditionPoint:
    """Parse a condition point from YAML data.

    Known spec fields are extracted, everything else goes to condition_params
    via Pydantic's extra="allow".
    """
    # Copy data for parsing
    parsed = dict(data)

    # Handle comparator enum separately
    if "comparator" in parsed:
        parsed["comparator"] = Comparator(parsed["comparator"].upper())

    return ConditionPoint.model_validate(parsed)


def _parse_test_requirement(data: dict[str, Any]) -> TestRequirement:
    """Parse a test requirement from YAML data."""
    parsed = dict(data)

    return TestRequirement(
        characteristic_ref=parsed.get("characteristic_ref"),
        conditions=parsed.get("conditions", {}),
        guardband_pct=parsed.get("guardband_pct", 0.0),
        priority=parsed.get("priority", "standard"),
        description=parsed.get("description"),
    )


def load_products_from_directory(specs_dir: Path) -> dict[str, Product]:
    """Load all product specifications from a directory.

    Args:
        specs_dir: Path to the specs directory.

    Returns:
        Dictionary mapping product ID to Product.
    """
    products = {}
    for path in specs_dir.glob("*.yaml"):
        if path.name.startswith("_"):
            continue  # Skip files starting with underscore
        try:
            product = load_product(path)
            products[product.id] = product
        except Exception as e:
            # Log warning but continue loading other products
            import warnings

            warnings.warn(f"Failed to load product from {path}: {e}")
    return products
