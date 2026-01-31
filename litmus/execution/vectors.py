"""Vector expansion for parametric testing.

A Vector represents a single combination of input values that a test executes against.
The framework expands vectors from config (product, zip, range, nested loops) and
iterates over them, calling the test function for each.
"""

from collections.abc import Iterator, Mapping
from decimal import Decimal
from itertools import product as itertools_product
from typing import Any

from litmus.utils.ranges import expand_numeric_range


class Vector(dict):
    """A dict subclass representing test input parameters with helper methods.

    Vectors are plain dicts with optional metadata fields:
    - _index: Position in the expansion (0-based)
    - _prev: Previous vector (for .changed() detection)

    Example:
        vector = Vector(voltage=3.3, current=0.1, _index=0)
        vector["voltage"]  # 3.3
        vector.changed("voltage")  # True if changed from previous vector
    """

    def changed(self, key: str) -> bool:
        """Check if a parameter changed from the previous vector.

        Useful for triggering actions (like prompts) only when outer loop
        variables change during iteration.

        Args:
            key: Parameter name to check.

        Returns:
            True if the value differs from the previous vector, or if there
            is no previous vector (first iteration).
        """
        prev = self.get("_prev")
        if prev is None:
            return True
        return self.get(key) != prev.get(key)

    def params(self) -> dict[str, Any]:
        """Return only the non-metadata parameters (keys not starting with _)."""
        return {k: v for k, v in self.items() if not k.startswith("_")}


def expand_list(vectors: list[dict[str, Any]]) -> list[Vector]:
    """Expand an explicit list of vectors.

    Args:
        vectors: List of parameter dictionaries.

    Returns:
        List of Vector objects with _index and _prev set.

    Example:
        >>> expand_list([{"voltage": 3.3}, {"voltage": 5.0}])
        [Vector(voltage=3.3, _index=0), Vector(voltage=5.0, _index=1, _prev=...)]
    """
    result = []
    for i, params in enumerate(vectors):
        v = Vector(params)
        v["_index"] = i
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result


def expand_product(**params: list[Any]) -> list[Vector]:
    """Expand parameters using Cartesian product.

    The order of kwargs determines loop nesting: first kwarg is outermost
    (slowest changing), last is innermost (fastest changing).

    Args:
        **params: Named parameters with their possible values as lists or range strings.

    Returns:
        List of Vector objects covering all combinations.

    Example:
        >>> expand_product(voltage=[3.3, 5.0], current=[0.1, 0.5])
        [
            Vector(voltage=3.3, current=0.1, _index=0),
            Vector(voltage=3.3, current=0.5, _index=1),
            Vector(voltage=5.0, current=0.1, _index=2),
            Vector(voltage=5.0, current=0.5, _index=3),
        ]
        >>> expand_product(voltage="3.3:5.0:0.1", load=[0.1, 0.5])  # Range string
    """
    if not params:
        return [Vector(_index=0)]

    keys = list(params.keys())
    # Expand string range syntax for each parameter
    values = []
    for k in keys:
        v = params[k]
        if isinstance(v, str):
            v = expand_numeric_range(v)
        values.append(v)

    result = []
    for i, combo in enumerate(itertools_product(*values)):
        v = Vector(zip(keys, combo))
        v["_index"] = i
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result


def expand_zip(**params: list[Any]) -> list[Vector]:
    """Expand parameters by zipping (pairing) them together.

    All parameter lists must have the same length.

    Args:
        **params: Named parameters with their possible values as lists or range strings.

    Returns:
        List of Vector objects with paired values.

    Raises:
        ValueError: If parameter lists have different lengths.

    Example:
        >>> expand_zip(voltage=[3.3, 5.0, 12.0], expected=[3.2, 4.9, 11.8])
        [
            Vector(voltage=3.3, expected=3.2, _index=0),
            Vector(voltage=5.0, expected=4.9, _index=1),
            Vector(voltage=12.0, expected=11.8, _index=2),
        ]
        >>> expand_zip(voltage="3.3:5.0:0.1", expected="3.2:4.9:0.1")  # Range strings
    """
    if not params:
        return [Vector(_index=0)]

    keys = list(params.keys())
    # Expand string range syntax for each parameter
    values = []
    for k in keys:
        v = params[k]
        if isinstance(v, str):
            v = expand_numeric_range(v)
        values.append(v)

    lengths = set(len(v) for v in values)
    if len(lengths) > 1:
        raise ValueError(
            f"All parameter lists must have the same length for zip expansion. "
            f"Got lengths: {dict(zip(keys, [len(v) for v in values]))}"
        )

    result = []
    for i, combo in enumerate(zip(*values)):
        v = Vector(zip(keys, combo))
        v["_index"] = i
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result


def expand_range(
    name: str,
    start: float | Decimal,
    stop: float | Decimal,
    step: float | Decimal | None = None,
    count: int | None = None,
) -> list[Vector]:
    """Expand a single parameter over a numeric range.

    Either step or count must be provided, but not both.

    Args:
        name: Parameter name.
        start: Starting value (inclusive).
        stop: Ending value (inclusive).
        step: Step size between values.
        count: Number of values to generate (evenly spaced).

    Returns:
        List of Vector objects with the parameter varying over the range.

    Raises:
        ValueError: If neither step nor count is provided, or both are.

    Example:
        >>> expand_range("voltage", 0.0, 5.0, step=1.0)
        [Vector(voltage=0.0), Vector(voltage=1.0), ..., Vector(voltage=5.0)]
    """
    if (step is None) == (count is None):
        raise ValueError("Exactly one of 'step' or 'count' must be provided")

    # Convert to Decimal for precise arithmetic
    start_d = Decimal(str(start))
    stop_d = Decimal(str(stop))

    values: list[Decimal] = []
    if step is not None:
        step_d = Decimal(str(step))
        current = start_d
        while current <= stop_d:
            values.append(current)
            current += step_d
    else:
        # count is not None
        if count == 1:
            values = [start_d]
        else:
            step_d = (stop_d - start_d) / Decimal(count - 1)
            values = [start_d + step_d * Decimal(i) for i in range(count)]

    result = []
    for i, val in enumerate(values):
        v = Vector({name: val, "_index": i})
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result


def _expand_loop_level(loop_spec: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Expand a single loop level specification.

    Handles both single-variable loops and zipped multi-variable groups.

    Args:
        loop_spec: Loop specification dict with one of:
            - name + values: Single variable with explicit list
            - name + range: Single variable with range spec
            - zip: List of variable specs that iterate together

    Yields:
        Dictionaries of parameter values for each iteration.
    """
    if "zip" in loop_spec:
        # Zipped group: multiple variables iterate together
        zip_specs = loop_spec["zip"]
        expanded_lists: list[list[dict[str, Any]]] = []

        for spec in zip_specs:
            var_name = spec["name"]
            if "values" in spec:
                values = spec["values"]
                # Handle string range syntax: "-40:85:25" or "0.1:0.5:0.1"
                if isinstance(values, str):
                    values = expand_numeric_range(values)
                expanded_lists.append([{var_name: v} for v in values])
            elif "range" in spec:
                range_spec = spec["range"]
                range_vectors = expand_range(
                    var_name,
                    start=range_spec["start"],
                    stop=range_spec["stop"],
                    step=range_spec.get("step"),
                    count=range_spec.get("count"),
                )
                expanded_lists.append([{var_name: v[var_name]} for v in range_vectors])
            else:
                raise ValueError(f"Loop spec must have 'values' or 'range': {spec}")

        # Verify all have same length
        lengths = [len(lst) for lst in expanded_lists]
        if len(set(lengths)) > 1:
            names = [spec["name"] for spec in zip_specs]
            raise ValueError(
                f"Zipped variables must have same length. Got: {dict(zip(names, lengths))}"
            )

        # Zip them together
        for items in zip(*expanded_lists):
            merged: dict[str, Any] = {}
            for item in items:
                merged.update(item)
            yield merged

    else:
        # Single variable loop
        var_name = loop_spec["name"]
        if "values" in loop_spec:
            values = loop_spec["values"]
            # Handle string range syntax: "-40:85:25" or "0.1:0.5:0.1"
            if isinstance(values, str):
                values = expand_numeric_range(values)
            for val in values:
                yield {var_name: val}
        elif "range" in loop_spec:
            range_spec = loop_spec["range"]
            range_vectors = expand_range(
                var_name,
                start=range_spec["start"],
                stop=range_spec["stop"],
                step=range_spec.get("step"),
                count=range_spec.get("count"),
            )
            for v in range_vectors:
                yield {var_name: v[var_name]}
        else:
            raise ValueError(f"Loop spec must have 'values' or 'range': {loop_spec}")


def expand_nested(loops: list[dict[str, Any]]) -> list[Vector]:
    """Expand nested loop specifications.

    Loop order matters: first item is outermost (slowest changing),
    last is innermost (fastest changing). This matches nested for-loop
    semantics and minimizes expensive transitions.

    Args:
        loops: List of loop specifications. Each spec has:
            - name: Variable name
            - values: List of values, OR
            - range: Dict with start, stop, step/count
            - prompt: Optional prompt config shown on each iteration
            - zip: List of specs that iterate together (alternative to name)

    Returns:
        List of Vector objects covering all combinations.

    Example:
        >>> expand_nested([
        ...     {"name": "temperature", "values": [-40, 25, 85]},
        ...     {"name": "voltage", "range": {"start": 3.0, "stop": 3.2, "step": 0.1}},
        ...     {"name": "load", "values": [0.0, 0.5, 1.0]},
        ... ])
        # Returns 3 * 3 * 3 = 27 vectors
    """
    if not loops:
        return [Vector(_index=0)]

    def _recurse(loop_index: int, current_params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Recursively expand nested loops."""
        if loop_index >= len(loops):
            yield dict(current_params)
            return

        loop_spec = loops[loop_index]
        for params in _expand_loop_level(loop_spec):
            merged = {**current_params, **params}
            yield from _recurse(loop_index + 1, merged)

    result = []
    for i, params in enumerate(_recurse(0, {})):
        v = Vector(params)
        v["_index"] = i
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result


def expand_vectors(config: Mapping[str, Any] | list[dict[str, Any]]) -> list[Vector]:
    """Expand vectors from a configuration dictionary or list.

    Supports multiple expansion modes:
    - Explicit list: Just a list of parameter dicts
    - product: Cartesian product of parameters
    - zip: Parallel iteration of parameters
    - range: Single parameter over numeric range (via nested)
    - nested: Nested loops with fine-grained control

    Args:
        config: Vector configuration with optional 'expand' key specifying mode,
            or a list of parameter dicts for explicit list mode.
            If no 'expand' key, assumes explicit list or single vector.

    Returns:
        List of Vector objects.

    Example configs:
        # Explicit list (as list)
        [{"voltage": 3.3}, {"voltage": 5.0}]

        # Product expansion
        {"expand": "product", "voltage": [3.3, 5.0], "current": [0.1, 0.5]}

        # Zip expansion
        {"expand": "zip", "voltage": [3.3, 5.0], "expected": [3.2, 4.9]}

        # Nested loops
        {"expand": "nested", "loops": [...]}
    """
    if not config:
        return [Vector(_index=0)]

    # Handle list input directly
    if isinstance(config, list):
        return expand_list(config)

    expand_mode = config.get("expand")

    if expand_mode is None:
        # Check if it's already a list of vectors
        if isinstance(config, list):
            return expand_list(config)
        # Single vector case
        return expand_list([dict(config)])

    if expand_mode == "product":
        # Extract parameter lists (exclude 'expand' key)
        params = {k: v for k, v in config.items() if k != "expand"}
        return expand_product(**params)

    if expand_mode == "zip":
        params = {k: v for k, v in config.items() if k != "expand"}
        return expand_zip(**params)

    if expand_mode == "range":
        # Range expansion for single parameter
        name = config.get("name")
        if not name:
            # Find the parameter name (not 'expand' or range keys)
            for k in config:
                if k not in ("expand", "start", "stop", "step", "count"):
                    name = k
                    break
            if not name:
                raise ValueError("Range expansion requires parameter name")

        range_config = config.get(name, config)
        if isinstance(range_config, dict):
            return expand_range(
                name,
                start=range_config["start"],
                stop=range_config["stop"],
                step=range_config.get("step"),
                count=range_config.get("count"),
            )
        else:
            # name key contains the range dict directly in config
            return expand_range(
                name,
                start=config["start"],
                stop=config["stop"],
                step=config.get("step"),
                count=config.get("count"),
            )

    if expand_mode == "nested":
        loops = config.get("loops", [])
        return expand_nested(loops)

    raise ValueError(f"Unknown expansion mode: {expand_mode}")
