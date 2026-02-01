"""Limit derivation from product specifications.

This module provides functions to derive test limits from product
characteristics and test requirements, including guardband application.
"""

from typing import Any

from litmus.config.models import Limit
from litmus.products.models import Characteristic, ConditionPoint, TestRequirement


def derive_limit(
    char: Characteristic,
    req: TestRequirement,
    conditions: dict[str, Any] | None = None,
    char_id: str | None = None,
) -> Limit:
    """Derive test limit from characteristic and requirement.

    This function:
    1. Finds the ConditionPoint matching the requirement's conditions
    2. Calculates bounds from nominal ± tolerance (or explicit limits)
    3. Applies guardband to tighten the limits
    4. Preserves the comparator from the spec
    5. Sets spec_id for structured traceability

    Args:
        char: The product characteristic containing spec values.
        req: The test requirement with conditions and guardband.
        conditions: Optional override for conditions (defaults to req.conditions).
        char_id: Optional characteristic ID (uses char.id if available, else req.characteristic_ref).

    Returns:
        A Limit object with derived low/high bounds and spec_id.

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

    # Determine spec_id for structured traceability
    # Priority: explicit char_id > char.id > req.characteristic_ref
    spec_id = char_id or getattr(char, "id", None) or req.characteristic_ref

    # Build spec reference for traceability (human-readable with conditions)
    spec_ref = _build_spec_ref(char, params)

    return Limit(
        low=limit_low,
        high=limit_high,
        nominal=point.nominal,
        units=char.units,
        comparator=point.comparator,
        spec_id=spec_id,
        spec_ref=spec_ref,
    )


def _calculate_bounds(point: ConditionPoint) -> tuple[float | None, float | None]:
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
    spec_low: float | None,
    spec_high: float | None,
    guardband_pct: float,
    comparator: str,
) -> tuple[float | None, float | None]:
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
    if guardband_pct == 0.0:
        return spec_low, spec_high

    # For range comparators, tighten both sides
    range_comparators = {"GELE", "GELT", "GTLE", "GTLT"}

    if comparator in range_comparators and spec_low is not None and spec_high is not None:
        # Calculate range and guardband amount
        range_size = spec_high - spec_low
        guardband_amount = range_size * guardband_pct / 100.0 / 2.0

        return spec_low + guardband_amount, spec_high - guardband_amount

    # For single-sided comparators, tighten the relevant limit
    if comparator in {"LE", "LT"} and spec_high is not None:
        # Upper limit only - tighten by percentage of the limit value
        guardband_amount = abs(spec_high) * guardband_pct / 100.0
        return spec_low, spec_high - guardband_amount

    if comparator in {"GE", "GT"} and spec_low is not None:
        # Lower limit only - tighten by percentage of the limit value
        guardband_amount = abs(spec_low) * guardband_pct / 100.0
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
    char_id: str | None = None,
) -> list[tuple[dict[str, Any], Limit]]:
    """Derive limits for all condition points matching a requirement.

    If the requirement specifies partial conditions (e.g., just temperature),
    this returns limits for all matching condition points.

    Args:
        char: The product characteristic.
        req: The test requirement.
        char_id: Optional characteristic ID for spec_id traceability.

    Returns:
        List of (conditions, limit) tuples for each matching condition point.
    """
    results = []

    for point in char.conditions:
        # Check if this point satisfies the requirement's conditions
        # (requirement params must exist in condition point)
        if point.satisfies(req.conditions):
            # Merge the full condition params for the limit derivation
            full_conditions = point.condition_params
            limit = derive_limit(char, req, full_conditions, char_id=char_id)
            results.append((full_conditions, limit))

    return results
