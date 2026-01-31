"""Range expansion utilities for pins, channels, and numeric values.

Supports SCPI-style inclusive ranges (hardware industry standard):
- "1:4" → [1, 2, 3, 4] (inclusive, unlike Python)
- "GPIO[0:7]" → ["GPIO0", "GPIO1", ..., "GPIO7"]
- "1,3:5,8" → [1, 3, 4, 5, 8] (non-contiguous)
- "GPIO[0,2,4:6]" → ["GPIO0", "GPIO2", "GPIO4", "GPIO5", "GPIO6"]
- "-40:125:55" → [-40, 15, 70, 125] (numeric with step)
- "0.1:0.5:0.1" → [0.1, 0.2, 0.3, 0.4, 0.5] (float with step)

Prior Art:
- SCPI (IEEE 488.2): (@1:4) → 1,2,3,4 (inclusive)
- Verilog/VHDL: [7:0] → 8-bit bus (inclusive)
- NI DAQmx: ai0:3 → 4 channels (inclusive)
- Python slice: [0:4] → 0,1,2,3 (exclusive - different!)
"""

from __future__ import annotations

import re
from decimal import Decimal


def expand_range(spec: str | list | int) -> list[str]:
    """Expand a range specification to a list of string items.

    Handles three input types:
    1. List: Pass through, converting items to strings
    2. String with prefix[range]: Expand prefix to each number (GPIO[0:2] → GPIO0, GPIO1, GPIO2)
    3. String with numeric range: Expand numbers (1:4 → 1, 2, 3, 4)
    4. Single value: Return as single-item list

    Args:
        spec: Range string, list of items, or single value

    Returns:
        List of expanded items as strings

    Examples:
        >>> expand_range("1:4")
        ['1', '2', '3', '4']
        >>> expand_range("GPIO[0:2]")
        ['GPIO0', 'GPIO1', 'GPIO2']
        >>> expand_range("GPIO[0,2,4:6]")
        ['GPIO0', 'GPIO2', 'GPIO4', 'GPIO5', 'GPIO6']
        >>> expand_range(["A", "B"])  # List pass-through
        ['A', 'B']
        >>> expand_range("TP_VOUT")  # Single item
        ['TP_VOUT']
        >>> expand_range(1)  # Single int
        ['1']
    """
    # List: pass through with string conversion
    if isinstance(spec, list):
        return [str(item) for item in spec]

    # Single int/float: convert to string list
    if isinstance(spec, (int, float)):
        return [str(spec)]

    # Must be string from here
    if not isinstance(spec, str):
        return [str(spec)]

    # Check for prefix[range] pattern: GPIO[0:7], CH[1:4], ai[0:15]
    prefix_match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\[(.+)\]$', spec)
    if prefix_match:
        prefix = prefix_match.group(1)
        range_part = prefix_match.group(2)
        numbers = _expand_int_range(range_part)
        return [f"{prefix}{n}" for n in numbers]

    # Pure numeric range with optional step: 1:4 or 1:10:2 or 1,3:5
    # Must start with digit or negative sign
    if re.match(r'^-?[\d]', spec) and ':' in spec:
        # Check if this looks like a numeric range (no letters except 'e' for scientific notation)
        if re.match(r'^[\d,:\s.\-eE]+$', spec):
            numbers = _expand_int_range(spec)
            return [str(n) for n in numbers]

    # Comma-separated without colons: 1,3,5
    if ',' in spec and ':' not in spec and re.match(r'^[\d,\s.\-]+$', spec):
        numbers = _expand_int_range(spec)
        return [str(n) for n in numbers]

    # Single item (no range pattern detected)
    return [spec]


def _expand_int_range(spec: str) -> list[int]:
    """Expand comma-separated ranges of integers.

    Uses inclusive ranges (SCPI-style):
    - "1:4" → [1, 2, 3, 4]
    - "1,3,5" → [1, 3, 5]
    - "1:3,5,7:9" → [1, 2, 3, 5, 7, 8, 9]

    Args:
        spec: Range specification string

    Returns:
        List of integers
    """
    result = []
    for part in spec.split(','):
        part = part.strip()
        if ':' in part:
            bounds = part.split(':')
            if len(bounds) == 2:
                # Simple range: 1:4 → 1, 2, 3, 4
                start, end = int(bounds[0]), int(bounds[1])
                step = 1 if start <= end else -1
                result.extend(range(start, end + step, step))
            # Note: start:stop:step format handled by expand_numeric_range for floats
        else:
            result.append(int(part))
    return result


def expand_numeric_range(spec: str | list) -> list[Decimal]:
    """Expand a numeric range specification to a list of Decimal values.

    Supports:
    - Explicit list: [0.1, 0.2, 0.3] → pass through
    - Simple range: "1:4" → [1, 2, 3, 4]
    - Range with step: "-40:125:55" → [-40, 15, 70, 125]
    - Float range: "0.1:0.5:0.1" → [0.1, 0.2, 0.3, 0.4, 0.5]
    - Comma-separated: "3.3,5.0,12.0" → [3.3, 5.0, 12.0]
    - Mixed: "0,0.5:2:0.5,5" → [0, 0.5, 1.0, 1.5, 2.0, 5]

    Args:
        spec: Range string or list of values

    Returns:
        List of Decimal values

    Examples:
        >>> expand_numeric_range("-40:125:55")
        [Decimal('-40'), Decimal('15'), Decimal('70'), Decimal('125')]
        >>> expand_numeric_range("0.1:0.5:0.1")
        [Decimal('0.1'), Decimal('0.2'), Decimal('0.3'), Decimal('0.4'), Decimal('0.5')]
        >>> expand_numeric_range([1, 2, 3])
        [Decimal('1'), Decimal('2'), Decimal('3')]
    """
    # List: pass through with Decimal conversion
    if isinstance(spec, list):
        return [Decimal(str(v)) for v in spec]

    # Single numeric value
    if isinstance(spec, (int, float, Decimal)):
        return [Decimal(str(spec))]

    # String parsing
    if not isinstance(spec, str):
        return [Decimal(str(spec))]

    result: list[Decimal] = []

    for part in spec.split(','):
        part = part.strip()
        if ':' in part:
            bounds = part.split(':')
            if len(bounds) == 2:
                # Simple range: 1:4 → 1, 2, 3, 4 (step=1)
                start = Decimal(bounds[0])
                stop = Decimal(bounds[1])
                step = Decimal('1') if start <= stop else Decimal('-1')
                result.extend(_generate_decimal_range(start, stop, step))
            elif len(bounds) == 3:
                # Range with step: -40:125:55
                start = Decimal(bounds[0])
                stop = Decimal(bounds[1])
                step = Decimal(bounds[2])
                result.extend(_generate_decimal_range(start, stop, step))
        else:
            result.append(Decimal(part))

    return result


def generate_numeric_range(
    start: float | Decimal,
    stop: float | Decimal,
    step: float | Decimal | None = None,
    count: int | None = None,
) -> list[Decimal]:
    """Generate a range of Decimal values with step or count.

    Exactly one of step or count must be provided. Range is inclusive of stop.

    Args:
        start: Starting value (inclusive)
        stop: Ending value (inclusive)
        step: Step size between values
        count: Number of values to generate (evenly spaced)

    Returns:
        List of Decimal values from start to stop (inclusive)

    Raises:
        ValueError: If neither step nor count provided, or both provided

    Examples:
        >>> generate_numeric_range(0, 5, step=1)
        [Decimal('0'), Decimal('1'), Decimal('2'), Decimal('3'), Decimal('4'), Decimal('5')]
        >>> generate_numeric_range(0, 1, count=3)
        [Decimal('0'), Decimal('0.5'), Decimal('1')]
    """
    if (step is None) == (count is None):
        raise ValueError("Exactly one of 'step' or 'count' must be provided")

    start_d = Decimal(str(start))
    stop_d = Decimal(str(stop))

    if step is not None:
        step_d = Decimal(str(step))
        return _generate_decimal_range(start_d, stop_d, step_d)
    else:
        # count is not None - generate evenly spaced values
        if count == 1:
            return [start_d]
        step_d = (stop_d - start_d) / Decimal(count - 1)
        return [start_d + step_d * Decimal(i) for i in range(count)]


def _generate_decimal_range(start: Decimal, stop: Decimal, step: Decimal) -> list[Decimal]:
    """Generate a range of Decimal values with step, inclusive of stop.

    Internal helper - use generate_numeric_range() for public API.

    Args:
        start: Starting value
        stop: Ending value (inclusive)
        step: Step size (must be non-zero)

    Returns:
        List of Decimal values from start to stop (inclusive)
    """
    if step == 0:
        raise ValueError("Step cannot be zero")

    result = []
    current = start

    # Handle direction
    if step > 0:
        while current <= stop:
            result.append(current)
            current += step
    else:
        while current >= stop:
            result.append(current)
            current += step

    # Ensure stop is included if we're very close (floating point tolerance)
    if result and abs(result[-1] - stop) > abs(step) / 2:
        # We might have stopped just before stop due to rounding
        if (step > 0 and result[-1] < stop) or (step < 0 and result[-1] > stop):
            result.append(stop)

    return result
