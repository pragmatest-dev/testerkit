"""Limit derivation from product specifications.

This module provides functions to derive test limits from product
characteristics and spec bands, including guardband application.
"""

from typing import Any

from litmus.models.capability import SpecBand
from litmus.models.enums import Comparator
from litmus.models.product import ProductCharacteristic
from litmus.models.test_config import Limit


def derive_limit(
    char: ProductCharacteristic,
    conditions: dict[str, Any] | None = None,
    guardband_pct: float = 0.0,
    comparator: Comparator = Comparator.GELE,
    limit_low: float | None = None,
    limit_high: float | None = None,
    char_id: str | None = None,
) -> Limit:
    """Derive test limit from characteristic at given conditions.

    This function:
    1. Finds the SpecBand matching the given conditions
    2. Calculates bounds from value ± accuracy (or explicit limits)
    3. Applies guardband to tighten the limits
    4. Sets spec_id for structured traceability

    Args:
        char: The product characteristic containing spec values.
        conditions: Operating point parameters (e.g., {"temperature": 25}).
        guardband_pct: Percentage to tighten limits (0-100).
        comparator: How to compare measured value against limits.
        limit_low: Explicit low limit override.
        limit_high: Explicit high limit override.
        char_id: Optional characteristic ID for spec_id traceability.

    Returns:
        A Limit object with derived low/high bounds and spec_id.

    Raises:
        ValueError: If no SpecBand matches the given conditions.
    """
    params = conditions or {}

    # Find matching spec band
    band = char.get_spec_at(params)
    if band is None:
        cond_str = ", ".join(f"{k}={v}" for k, v in params.items())
        avail = [dict(s.when) for s in char.specs]
        raise ValueError(f"No spec band matches: {cond_str}. Available when clauses: {avail}")

    # Calculate spec bounds from SpecBand
    spec_low, spec_high = _calculate_bounds(band, comparator, limit_low, limit_high)

    # Apply guardband (tighten limits)
    final_low, final_high = _apply_guardband(spec_low, spec_high, guardband_pct, comparator.value)

    spec_id = char_id
    spec_ref = _build_spec_ref(char, params)

    nominal = float(band.value) if isinstance(band.value, (int, float)) else None

    return Limit(
        low=final_low,
        high=final_high,
        nominal=nominal,
        units=char.units or "",
        comparator=comparator,
        spec_id=spec_id,
        spec_ref=spec_ref,
    )


def _calculate_bounds(
    band: SpecBand,
    comparator: Comparator,
    limit_low: float | None,
    limit_high: float | None,
) -> tuple[float | None, float | None]:
    """Calculate spec bounds from a SpecBand.

    Priority:
    1. Explicit limit_low/limit_high overrides
    2. SpecBand.value ± SpecBand.accuracy (via total_uncertainty)
    3. SpecBand.value alone (exact match)

    For single-sided comparators (LE, GE), only the relevant bound is set.
    """
    # Explicit limits take precedence
    if limit_low is not None or limit_high is not None:
        return limit_low, limit_high

    if band.value is None or not isinstance(band.value, (int, float)):
        return None, None

    val: float = band.value

    # Single-sided comparators
    if comparator in (Comparator.LE, Comparator.LT):
        return None, val
    if comparator in (Comparator.GE, Comparator.GT):
        return val, None

    # Range comparator: derive from accuracy
    if band.accuracy is not None:
        uncertainty = band.accuracy.total_uncertainty(val, val)
        return val - uncertainty, val + uncertainty

    # No accuracy — exact value
    return val, val


def _apply_guardband(
    spec_low: float | None,
    spec_high: float | None,
    guardband_pct: float,
    comparator: str,
) -> tuple[float | None, float | None]:
    """Apply guardband to tighten spec limits.

    Guardband reduces the acceptable range by moving limits inward.
    """
    if guardband_pct == 0.0:
        return spec_low, spec_high

    range_comparators = {"GELE", "GELT", "GTLE", "GTLT"}

    if comparator in range_comparators and spec_low is not None and spec_high is not None:
        range_size = spec_high - spec_low
        guardband_amount = range_size * guardband_pct / 100.0 / 2.0
        return spec_low + guardband_amount, spec_high - guardband_amount

    if comparator in {"LE", "LT"} and spec_high is not None:
        guardband_amount = abs(spec_high) * guardband_pct / 100.0
        return spec_low, spec_high - guardband_amount

    if comparator in {"GE", "GT"} and spec_low is not None:
        guardband_amount = abs(spec_low) * guardband_pct / 100.0
        return spec_low + guardband_amount, spec_high

    return spec_low, spec_high


def _build_spec_ref(char: ProductCharacteristic, conditions: dict[str, Any]) -> str:
    """Build a spec reference string for traceability."""
    base_ref = char.datasheet_ref or "spec"
    if conditions:
        cond_str = ", ".join(f"{k}={v}" for k, v in sorted(conditions.items()))
        return f"{base_ref} @ {cond_str}"
    return base_ref
