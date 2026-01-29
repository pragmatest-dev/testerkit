"""YAML loading for product specifications."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from litmus.capabilities.models import Comparator, Direction, Domain, SignalType
from litmus.products.models import (
    Characteristic,
    ConditionPoint,
    Product,
    TestRequirement,
)


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
        characteristics=characteristics,
        test_requirements=test_requirements,
    )


def _parse_characteristic(data: dict[str, Any]) -> Characteristic:
    """Parse a characteristic from YAML data."""
    # Parse direction enum (values are lowercase: input, output, bidir)
    direction = Direction(data["direction"].lower())

    # Parse domain enum (values are lowercase: voltage, current, etc.)
    domain = Domain(data["domain"].lower())

    # Parse signal types (values are lowercase: dc, ac, etc.)
    signal_types_raw = data.get("signal_types", ["dc"])
    signal_types = [SignalType(st.lower()) for st in signal_types_raw]

    # Parse conditions
    conditions = []
    for cond_data in data.get("conditions", []):
        conditions.append(_parse_condition_point(cond_data))

    return Characteristic(
        direction=direction,
        domain=domain,
        signal_types=signal_types,
        units=data["units"],
        datasheet_ref=data.get("datasheet_ref"),
        schematic_ref=data.get("schematic_ref"),
        conditions=conditions,
    )


def _parse_condition_point(data: dict[str, Any]) -> ConditionPoint:
    """Parse a condition point from YAML data.

    Known spec fields are extracted, everything else goes to condition_params
    via Pydantic's extra="allow".
    """
    # Extract known spec fields
    spec_fields = {}

    if "nominal" in data:
        spec_fields["nominal"] = Decimal(str(data["nominal"]))
    if "tolerance_pct" in data:
        spec_fields["tolerance_pct"] = Decimal(str(data["tolerance_pct"]))
    if "tolerance_abs" in data:
        spec_fields["tolerance_abs"] = Decimal(str(data["tolerance_abs"]))
    if "limit_low" in data:
        spec_fields["limit_low"] = Decimal(str(data["limit_low"]))
    if "limit_high" in data:
        spec_fields["limit_high"] = Decimal(str(data["limit_high"]))
    if "comparator" in data:
        spec_fields["comparator"] = Comparator(data["comparator"].upper())

    # All other fields are condition parameters (temperature, load, etc.)
    known_fields = {
        "nominal",
        "tolerance_pct",
        "tolerance_abs",
        "limit_low",
        "limit_high",
        "comparator",
    }
    condition_params = {k: v for k, v in data.items() if k not in known_fields}

    # Combine spec fields with condition params
    all_fields = {**spec_fields, **condition_params}

    return ConditionPoint.model_validate(all_fields)


def _parse_test_requirement(data: dict[str, Any]) -> TestRequirement:
    """Parse a test requirement from YAML data."""
    guardband_pct = Decimal("0")
    if "guardband_pct" in data:
        guardband_pct = Decimal(str(data["guardband_pct"]))

    return TestRequirement(
        characteristic_ref=data.get("characteristic_ref"),
        conditions=data.get("conditions", {}),
        guardband_pct=guardband_pct,
        priority=data.get("priority", "standard"),
        description=data.get("description"),
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
