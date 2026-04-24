"""Vector expansion for the YAML sidecar format.

Thin wrapper around :func:`litmus.execution.vectors.expand_vectors` that
accepts the sidecar-style mapping::

    {list: [{...}, {...}]}          # explicit rows
    {product: {vin: [...], ...}}    # cartesian product
    {zip: {vin: [...], expected: [...]}}  # lock-step zip

The underlying engine already supports range strings and nested
composition via a ``vectors`` sub-key — this wrapper just translates the
top-level sidecar key (``list`` / ``product`` / ``zip``) to the
``expand``-keyed form that :func:`expand_vectors` understands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from litmus.execution.vectors import Vector, expand_vectors

_SUPPORTED_KEYS = ("list", "product", "zip")


def expand(block: Mapping[str, Any]) -> list[Vector]:
    """Expand a sidecar block into a list of :class:`Vector`.

    Args:
        block: Mapping with exactly one of ``list``, ``product``, ``zip``
            as its expansion key.

    Returns:
        Expanded vectors, each with ``_index`` (and ``_prev`` when not
        the first) metadata populated by the underlying engine.

    Raises:
        ValueError: If the block has no recognized expansion key or has
            more than one.
    """
    if not block:
        return [Vector(_index=0)]

    keys = [k for k in _SUPPORTED_KEYS if k in block]
    if not keys:
        raise ValueError(
            f"Vector block must contain one of {_SUPPORTED_KEYS}. Got keys: {sorted(block)}"
        )
    if len(keys) > 1:
        raise ValueError(f"Vector block must contain exactly one expansion key; got {keys}")

    mode = keys[0]
    payload = block[mode]

    if mode == "list":
        if not isinstance(payload, list):
            raise ValueError(
                f"'list' expansion expects a list of dicts; got {type(payload).__name__}"
            )
        return expand_vectors(payload)

    if not isinstance(payload, Mapping):
        raise ValueError(
            f"'{mode}' expansion expects a mapping of param → values; got {type(payload).__name__}"
        )

    return expand_vectors({"expand": mode, **payload})
