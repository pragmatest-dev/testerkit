"""YAML loading for product specifications."""

from pathlib import Path
from typing import Any

import yaml

from litmus.config.models import (
    Comparator,
    MeasurementFunction,
    SignalParameter,
)
from litmus.products.models import (
    BusSignal,
    Characteristic,
    ConditionPoint,
    Pin,
    Product,
    SignalGroup,
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
            function: dc_voltage
            direction: output
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
    from litmus.products.models import PinRole

    role = PinRole.SIGNAL
    if "role" in data:
        role = PinRole(data["role"].lower())

    return Pin(
        name=data["name"],
        net=data.get("net"),
        role=role,
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
    """Parse a characteristic from YAML data.

    Supports the function-based format:
        function: dc_voltage
        direction: output
        units: V
        parameters:
          voltage:
            value: 3.3
            units: V
    """
    from litmus.config.models import Direction

    direction = Direction(data["direction"].lower())
    function = MeasurementFunction(data.get("function", "dc_voltage").lower())

    # Parse signal parameters
    parameters: dict[str, SignalParameter] = {}
    for param_name, param_data in data.get("parameters", {}).items():
        parameters[param_name] = _parse_signal_parameter(param_data)

    # Parse conditions
    conditions = []
    for cond_data in data.get("conditions", []):
        conditions.append(_parse_condition_point(cond_data))

    return Characteristic(
        function=function,
        direction=direction,
        parameters=parameters,
        units=data["units"],
        # Physical interface fields
        pin=data.get("pin"),
        pins=data.get("pins", []),
        net=data.get("net"),
        signal_group=data.get("signal_group"),
        channel=data.get("channel"),
        # Traceability
        datasheet_ref=data.get("datasheet_ref"),
        conditions=conditions,
    )


def _parse_signal_parameter(data: dict[str, Any]) -> SignalParameter:
    """Parse a SignalParameter from YAML data."""
    from litmus.config.models import (
        AccuracySpec,
        ParameterRole,
        RangeSpec,
        ResolutionSpec,
    )

    range_spec = None
    if "range" in data:
        r = data["range"]
        range_spec = RangeSpec(min=r.get("min"), max=r.get("max"), units=r.get("units", ""))

    accuracy_spec = None
    if "accuracy" in data:
        a = data["accuracy"]
        accuracy_spec = AccuracySpec(
            pct_reading=a.get("pct_reading"),
            pct_range=a.get("pct_range"),
            absolute=a.get("absolute"),
        )

    resolution_spec = None
    if "resolution" in data:
        r = data["resolution"]
        resolution_spec = ResolutionSpec(
            bits=r.get("bits"), digits=r.get("digits"), value=r.get("value"), units=r.get("units")
        )

    role = ParameterRole.CONTROLLABLE
    if "role" in data:
        role = ParameterRole(data["role"])

    return SignalParameter(
        range=range_spec,
        accuracy=accuracy_spec,
        resolution=resolution_spec,
        value=data.get("value"),
        units=data.get("units"),
        role=role,
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
