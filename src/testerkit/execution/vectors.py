"""Vector expansion for parametric testing.

A Vector represents a single combination of input values that a test
executes against. The framework expands vectors from config and iterates
over them, calling the test function for each.

Two expansion modes:
- ``product`` — Cartesian product (default)
- ``zip`` — lock-step pairing

Recursive composition via the ``vectors`` key:

.. code-block:: yaml

    vectors:
      expand: product
      temperature: [-20, 25, 85]
      vectors:
        expand: zip
        voltage: [3.3, 5.0]
        expected: [3.2, 4.9]

Range strings (``"start:stop:step"``) work anywhere a value list is expected.
"""

from collections.abc import Mapping
from itertools import product as itertools_product
from typing import Any

from testerkit.utils.ranges import expand_numeric_range

_RESERVED_KEYS = frozenset({"expand", "vectors"})


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


def _expand_values(values: Any) -> list[Any]:
    """Expand a parameter's values — range strings become lists."""
    if isinstance(values, str):
        return expand_numeric_range(values)
    if isinstance(values, list):
        return values
    return [values]


def _expand_level(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand a single level of vector config (product or zip).

    Handles the ``vectors`` key for recursive composition: expands the
    sub-block and cross-products it with the current level.
    """
    expand_mode = config.get("expand", "product")
    sub_config = config.get("vectors")

    # Collect parameter keys (everything except reserved keys)
    params: dict[str, list[Any]] = {}
    for k, v in config.items():
        if k not in _RESERVED_KEYS:
            params[k] = _expand_values(v)

    # Expand this level
    if expand_mode == "product":
        level_results = _expand_product(params)
    elif expand_mode == "zip":
        level_results = _expand_zip(params)
    else:
        raise ValueError(f"Unknown expansion mode: {expand_mode!r} (use 'product' or 'zip')")

    # No sub-block — done
    if sub_config is None:
        return level_results

    # Recurse into sub-block
    sub_results = _expand_level(sub_config)

    # Cross-product current level with sub-block
    combined = []
    for outer in level_results:
        for inner in sub_results:
            combined.append({**outer, **inner})
    return combined


def _expand_product(params: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of parameter lists."""
    if not params:
        return [{}]
    keys = list(params.keys())
    values = [params[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools_product(*values)]


def _expand_zip(params: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Lock-step pairing of parameter lists."""
    if not params:
        return [{}]
    keys = list(params.keys())
    values = [params[k] for k in keys]
    lengths = {len(v) for v in values}
    if len(lengths) > 1:
        detail = {k: len(v) for k, v in zip(keys, values)}
        raise ValueError(f"Zip expansion requires equal-length lists. Got: {detail}")
    return [dict(zip(keys, combo)) for combo in zip(*values)]


def _finalize(raw: list[dict[str, Any]]) -> list[Vector]:
    """Add _index and _prev metadata to raw dicts."""
    result: list[Vector] = []
    for i, params in enumerate(raw):
        v = Vector(params)
        v["_index"] = i
        if i > 0:
            v["_prev"] = result[i - 1]
        result.append(v)
    return result


def expand_vectors(config: Mapping[str, Any] | list[dict[str, Any]]) -> list[Vector]:
    """Expand vectors from a configuration dictionary or list.

    Supports:
    - Explicit list: ``[{"voltage": 3.3}, {"voltage": 5.0}]``
    - Product: ``{"expand": "product", "vin": [3.3, 5.0], "load": [0.1, 0.5]}``
    - Zip: ``{"expand": "zip", "vin": [3.3, 5.0], "expected": [3.2, 4.9]}``
    - Recursive: any level can have a ``vectors`` sub-block
    - Range strings: ``"4.5:5.5:0.1"`` anywhere a list is expected

    Args:
        config: Vector configuration dict or explicit list of parameter dicts.

    Returns:
        List of Vector objects.
    """
    if not config:
        return [Vector(_index=0)]

    # Explicit list of vectors
    if isinstance(config, list):
        return _finalize(config)

    # No expand key and no vectors sub-block — treat as single vector
    if "expand" not in config and "vectors" not in config:
        return _finalize([{k: v for k, v in config.items()}])

    return _finalize(_expand_level(config))


# --- Public aliases kept for import compatibility ---


def expand_product(**params: Any) -> list[Vector]:
    """Expand parameters using Cartesian product.

    Args:
        **params: Named parameters with values as lists or range strings.

    Example:
        >>> expand_product(voltage=[3.3, 5.0], current=[0.1, 0.5])
    """
    expanded = {k: _expand_values(v) for k, v in params.items()}
    return _finalize(_expand_product(expanded))


def expand_zip(**params: Any) -> list[Vector]:
    """Expand parameters by zipping (pairing) them together.

    Args:
        **params: Named parameters with values as lists or range strings.

    Example:
        >>> expand_zip(voltage=[3.3, 5.0], expected=[3.2, 4.9])
    """
    expanded = {k: _expand_values(v) for k, v in params.items()}
    return _finalize(_expand_zip(expanded))
