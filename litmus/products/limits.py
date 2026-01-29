"""Limit derivation from product specifications.

This module provides functions to derive test limits from product
characteristics and test requirements, including guardband application.
"""

from decimal import Decimal
from typing import Any

from litmus.config.models import Limit
from litmus.products.models import Characteristic, ConditionPoint, TestRequirement


def derive_limit(
    char: Characteristic,
    req: TestRequirement,
    conditions: dict[str, Any] | None = None,
) -> Limit:
    """Derive test limit from characteristic and requirement.

    This function:
    1. Finds the ConditionPoint matching the requirement's conditions
    2. Calculates bounds from nominal ± tolerance (or explicit limits)
    3. Applies guardband to tighten the limits
    4. Preserves the comparator from the spec

    Args:
        char: The product characteristic containing spec values.
        req: The test requirement with conditions and guardband.
        conditions: Optional override for conditions (defaults to req.conditions).

    Returns:
        A Limit object with derived low/high bounds.

    Raises:
        ValueError: If no condition point matches the given conditions.
    """
    # Use provided conditions or fall back to requirement's conditions
    params = conditions if conditions is not None else req.conditions

    # Find matching condition point
    point = char.get_at_conditions(params)
    if point is None:
        cond_str = ", ".join(f"{k}={v}" for k, v in params.items())
        raise ValueError(
            f"No condition point matches: {cond_str}. "
            f"Available conditions: {[p.condition_params for p in char.conditions]}"
        )

    # Calculate spec bounds
    spec_low, spec_high = _calculate_bounds(point)

    # Apply guardband (tighten limits)
    limit_low, limit_high = _apply_guardband(
        spec_low, spec_high, req.guardband_pct, point.comparator.value
    )

    # Build spec reference for traceability
    spec_ref = _build_spec_ref(char, params)

    return Limit(
        low=limit_low,
        high=limit_high,
        nominal=point.nominal,
        units=char.units,
        comparator=point.comparator,
        spec_ref=spec_ref,
    )


def _calculate_bounds(point: ConditionPoint) -> tuple[Decimal | None, Decimal | None]:
    """Calculate spec bounds from a condition point.

    Priority:
    1. Explicit limit_low/limit_high take precedence
    2. Otherwise calculate from nominal ± tolerance

    Returns:
        Tuple of (low, high) bounds.
    """
    # Use the computed properties which handle the priority logic
    return point.low, point.high


def _apply_guardband(
    spec_low: Decimal | None,
    spec_high: Decimal | None,
    guardband_pct: Decimal,
    comparator: str,
) -> tuple[Decimal | None, Decimal | None]:
    """Apply guardband to tighten spec limits.

    Guardband reduces the acceptable range by moving limits inward:
    - For range comparators (GELE, etc.): both limits move toward center
    - For single-sided comparators (LE, GE, etc.): only the relevant limit tightens

    The guardband percentage is applied to the range, not the individual limits.
    Formula: new_range = original_range * (1 - guardband_pct / 100)

    Args:
        spec_low: Original lower bound.
        spec_high: Original upper bound.
        guardband_pct: Percentage to tighten (0-100).
        comparator: The comparator type (affects which limits are tightened).

    Returns:
        Tuple of (new_low, new_high) with guardband applied.
    """
    if guardband_pct == Decimal("0"):
        return spec_low, spec_high

    # For range comparators, tighten both sides
    range_comparators = {"GELE", "GELT", "GTLE", "GTLT"}

    if comparator in range_comparators and spec_low is not None and spec_high is not None:
        # Calculate range and guardband amount
        range_size = spec_high - spec_low
        guardband_amount = range_size * guardband_pct / Decimal("100") / Decimal("2")

        return spec_low + guardband_amount, spec_high - guardband_amount

    # For single-sided comparators, tighten the relevant limit
    if comparator in {"LE", "LT"} and spec_high is not None:
        # Upper limit only - tighten by percentage of the limit value
        guardband_amount = abs(spec_high) * guardband_pct / Decimal("100")
        return spec_low, spec_high - guardband_amount

    if comparator in {"GE", "GT"} and spec_low is not None:
        # Lower limit only - tighten by percentage of the limit value
        guardband_amount = abs(spec_low) * guardband_pct / Decimal("100")
        return spec_low + guardband_amount, spec_high

    # EQ/NE comparators or missing bounds - no guardband applied
    return spec_low, spec_high


def _build_spec_ref(char: Characteristic, conditions: dict[str, Any]) -> str:
    """Build a spec reference string for traceability.

    Format: "datasheet_ref @ condition1=value1, condition2=value2"

    Args:
        char: The characteristic (contains datasheet_ref).
        conditions: The condition parameters used.

    Returns:
        Formatted spec reference string.
    """
    base_ref = char.datasheet_ref or "spec"
    if conditions:
        cond_str = ", ".join(f"{k}={v}" for k, v in sorted(conditions.items()))
        return f"{base_ref} @ {cond_str}"
    return base_ref


def derive_limits_for_requirement(
    char: Characteristic,
    req: TestRequirement,
) -> list[tuple[dict[str, Any], Limit]]:
    """Derive limits for all condition points matching a requirement.

    If the requirement specifies partial conditions (e.g., just temperature),
    this returns limits for all matching condition points.

    Args:
        char: The product characteristic.
        req: The test requirement.

    Returns:
        List of (conditions, limit) tuples for each matching condition point.
    """
    results = []

    for point in char.conditions:
        # Check if this point matches the requirement's conditions
        if point.matches(req.conditions):
            # Merge the full condition params for the limit derivation
            full_conditions = point.condition_params
            limit = derive_limit(char, req, full_conditions)
            results.append((full_conditions, limit))

    return results
