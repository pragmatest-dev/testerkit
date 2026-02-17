"""YAML loading for product specifications."""

from pathlib import Path
from typing import Any

import yaml

from litmus.config.models import (
    AccuracySpec,
    Attribute,
    CompareMode,
    Condition,
    Control,
    Signal,
    MeasurementFunction,
    RangeSpec,
    ResolutionSpec,
    SpecBand,
)
from litmus.products.models import (
    BusSignal,
    Pin,
    Product,
    ProductCharacteristic,
    SignalGroup,
)

_MAX_INHERIT_DEPTH = 5


def load_product(path: Path, products_dir: Path | None = None) -> Product:
    """Load a product specification from YAML, resolving inheritance.

    If the product has a ``base`` field, the base product's YAML is loaded and
    merged at section level before parsing.  Sections present in the variant
    completely replace the base's version (no deep merge).

    Args:
        path: Path to the product YAML file.
        products_dir: Directory to search for base products.  Defaults to
            the grandparent of *path* (assumes products/{id}/spec.yaml layout),
            falling back to *path*.parent.

    Returns:
        Product object with characteristics and SpecBands.

    Raises:
        FileNotFoundError: If the YAML file doesn't exist.
        yaml.YAMLError: If the YAML is malformed.
        pydantic.ValidationError: If the data doesn't match the model.
        ValueError: On circular or missing base inheritance.
    """
    if products_dir is None:
        # Assume products/{id}/spec.yaml → products_dir is grandparent
        candidate = path.parent.parent
        if candidate.name == "products" or any(candidate.iterdir()):
            products_dir = candidate
        else:
            products_dir = path.parent

    data = _load_with_inheritance(path, products_dir, seen=set(), depth=0)

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

    return Product(
        id=product_id,
        name=product_name,
        part_number=product_data.get("part_number"),
        base=product_data.get("base"),
        description=product_data.get("description"),
        revision=product_data.get("revision"),
        datasheet=product_data.get("datasheet"),
        schematic=product_data.get("schematic"),
        pins=pins,
        signal_groups=signal_groups,
        characteristics=characteristics,
    )


def _load_with_inheritance(
    path: Path,
    products_dir: Path,
    seen: set[str],
    depth: int,
) -> dict[str, Any]:
    """Load raw YAML and recursively merge base products.

    Merge semantics are section-level override: if the variant provides
    ``characteristics:``, ``pins:``, etc., those replace the base's version
    entirely.  Header fields (name, description, revision, part_number,
    datasheet, schematic) are inherited when absent in the variant.

    Raises:
        ValueError: On circular inheritance or missing base.
    """
    if depth > _MAX_INHERIT_DEPTH:
        raise ValueError(
            f"Product inheritance depth exceeds {_MAX_INHERIT_DEPTH} for {path}"
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    product_data = data.get("product", {})
    product_id = product_data.get("id", path.stem)
    base_ref = product_data.get("base")

    if not base_ref:
        return data

    # Cycle detection
    if product_id in seen:
        raise ValueError(
            f"Circular product inheritance: {product_id!r} already in chain {seen}"
        )
    seen.add(product_id)

    # Locate base file: try {base_ref}/spec.yaml then {base_ref}.yaml
    base_path = products_dir / base_ref / "spec.yaml"
    if not base_path.exists():
        base_path = products_dir / f"{base_ref}.yaml"
    if not base_path.exists():
        raise ValueError(
            f"Base product {base_ref!r} not found "
            f"(referenced by {product_id!r} in {path})"
        )

    base_data = _load_with_inheritance(base_path, products_dir, seen, depth + 1)
    return _merge_product_data(base_data, data)


def _merge_product_data(
    base: dict[str, Any], variant: dict[str, Any]
) -> dict[str, Any]:
    """Merge base and variant product YAML with section-level override.

    Rules:
    - Header fields inherited from base when absent in variant:
      name, description, revision, part_number, datasheet, schematic
    - id and base always come from variant
    - pins, signal_groups, characteristics, test_requirements:
      variant replaces entirely if present, else inherited from base
    """
    base_product = dict(base.get("product", {}))
    variant_product = dict(variant.get("product", {}))

    merged_product: dict[str, Any] = {}

    # Inherit header fields from base
    for key in ("name", "description", "revision", "part_number", "datasheet", "schematic"):
        if key in base_product:
            merged_product[key] = base_product[key]

    # Variant overrides everything it provides
    merged_product.update(variant_product)

    merged: dict[str, Any] = {"product": merged_product}

    # Section-level: variant replaces if present, else inherit base
    for section in ("pins", "signal_groups", "characteristics"):
        if section in variant:
            merged[section] = variant[section]
        elif section in base:
            merged[section] = base[section]

    return merged


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


def _parse_characteristic(data: dict[str, Any]) -> ProductCharacteristic:
    """Parse a characteristic from YAML data.

    Supports both new format (signals/conditions/controls/attributes)
    and legacy format (parameters with role tags).
    """
    from litmus.config.models import Direction

    direction = Direction(data["direction"].lower())
    function = MeasurementFunction(data.get("function", "dc_voltage").lower())

    # Parse typed parameter dicts
    signals: dict[str, Signal] = {}
    conditions: dict[str, Condition] = {}
    controls: dict[str, Control] = {}
    attributes: dict[str, Attribute] = {}

    for name, d in (data.get("signals") or {}).items():
        signals[name] = _parse_signal(d or {})
    conds_raw = data.get("conditions")
    if isinstance(conds_raw, dict):
        for name, d in conds_raw.items():
            conditions[name] = _parse_condition(d or {})
    for name, d in (data.get("controls") or {}).items():
        controls[name] = _parse_control(d or {})
    for name, d in (data.get("attributes") or {}).items():
        attributes[name] = _parse_attribute(d or {})

    # Parse specs (SpecBand list)
    specs = []
    for spec_data in data.get("specs", []):
        specs.append(_parse_product_spec_band(spec_data))

    return ProductCharacteristic(
        function=function,
        direction=direction,
        signals=signals,
        conditions=conditions,
        controls=controls,
        attributes=attributes,
        units=data["units"],
        # Physical interface fields
        pin=data.get("pin"),
        pins=data.get("pins", []),
        net=data.get("net"),
        signal_group=data.get("signal_group"),
        # Traceability
        datasheet_ref=data.get("datasheet_ref"),
        specs=specs,
    )


def _parse_signal(data: dict[str, Any]) -> Signal:
    """Parse a Signal from YAML data."""
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

    specs = None
    if "specs" in data:
        specs = [_parse_product_spec_band(s) for s in data["specs"]]

    return Signal(
        range=range_spec,
        accuracy=accuracy_spec,
        resolution=resolution_spec,
        value=data.get("value"),
        units=data.get("units"),
        specs=specs,
    )


def _parse_condition(data: dict[str, Any]) -> Condition:
    """Parse a Condition from YAML data."""
    range_spec = None
    if "range" in data:
        r = data["range"]
        range_spec = RangeSpec(min=r.get("min"), max=r.get("max"), units=r.get("units", ""))
    return Condition(range=range_spec)


def _parse_control(data: dict[str, Any]) -> Control:
    """Parse a Control from YAML data."""
    range_spec = None
    if "range" in data:
        r = data["range"]
        range_spec = RangeSpec(min=r.get("min"), max=r.get("max"), units=r.get("units", ""))
    return Control(
        range=range_spec,
        options=data.get("options"),
        units=data.get("units"),
        default=data.get("default"),
    )


def _parse_attribute(data: dict[str, Any]) -> Attribute:
    """Parse an Attribute from YAML data."""
    compare = None
    if "compare" in data:
        compare = CompareMode(data["compare"])
    return Attribute(
        value=data.get("value", 0),
        units=data.get("units"),
        compare=compare,
    )


def _parse_product_spec_band(data: dict[str, Any]) -> SpecBand:
    """Parse a product SpecBand from YAML data.

    Expected format:
        when:
          temperature: {min: 25, max: 25, units: degC}
          load: {min: 0.1, max: 0.1, units: A}
        value: 3.3
        accuracy: {pct_reading: 2.0}
    """
    when: dict[str, RangeSpec] = {}
    for key, val in data.get("when", {}).items():
        if isinstance(val, dict):
            when[key] = RangeSpec(
                min=val.get("min"), max=val.get("max"), units=val.get("units", "")
            )
        else:
            # Scalar shorthand: temperature: 25 → {min: 25, max: 25}
            when[key] = RangeSpec(min=float(val), max=float(val), units="")

    accuracy = None
    if "accuracy" in data:
        a = data["accuracy"]
        accuracy = AccuracySpec(
            pct_reading=a.get("pct_reading"),
            pct_range=a.get("pct_range"),
            absolute=a.get("absolute"),
        )

    range_spec = None
    if "range" in data:
        rng = data["range"]
        range_spec = RangeSpec(
            min=rng.get("min"), max=rng.get("max"), units=rng.get("units", "")
        )

    return SpecBand(
        when=when,
        range=range_spec,
        value=data.get("value"),
        accuracy=accuracy,
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
            product = load_product(path, products_dir=specs_dir)
            products[product.id] = product
        except Exception as e:
            # Log warning but continue loading other products
            import warnings

            warnings.warn(f"Failed to load product from {path}: {e}")
    return products
