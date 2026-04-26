"""Inline list-builders for ``litmus_vectors`` and other list positions.

These are the Python-callable counterparts to the YAML range expanders
(``{linspace: [start, stop, num]}`` etc.). YAML can't call functions,
so YAML uses dict-with-known-key shape; inline Python calls these
helpers directly so users get IDE autocomplete + signature help::

    from litmus import linspace, paired

    @pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 11))
    def test_x(vin): ...

    @paired(vin=[3, 4], vout=[5, 6])
    def test_x(vin, vout): ...

Each function delegates to the same underlying primitive the YAML
expander uses, so behavior is identical across surfaces.
"""

from __future__ import annotations

from typing import Any, TypeVar

import numpy as np
import pytest

T = TypeVar("T")


def linspace(start: float, stop: float, num: int) -> list[float]:
    """Evenly-spaced points from ``start`` to ``stop`` (inclusive).

    YAML equivalent: ``{linspace: [start, stop, num]}``.
    """
    return np.linspace(start, stop, num).tolist()


def arange(start: float, stop: float, step: float = 1.0) -> list[float]:
    """Evenly-spaced points from ``start`` to ``stop`` (exclusive) by ``step``.

    YAML equivalent: ``{arange: [start, stop, step]}``.
    """
    return np.arange(start, stop, step).tolist()


def logspace(start: float, stop: float, num: int) -> list[float]:
    """Log-spaced points (base 10) from ``10**start`` to ``10**stop``.

    YAML equivalent: ``{logspace: [start, stop, num]}``.
    """
    return np.logspace(start, stop, num).tolist()


def geomspace(start: float, stop: float, num: int) -> list[float]:
    """Geometrically-spaced points from ``start`` to ``stop``.

    YAML equivalent: ``{geomspace: [start, stop, num]}``.
    """
    return np.geomspace(start, stop, num).tolist()


def repeat(value: T, n: int) -> list[T]:
    """A list of ``n`` copies of ``value``.

    YAML equivalent: ``{repeat: [value, n]}``. Inline you can also write
    ``[value] * n`` directly; this exists for symmetry with the YAML
    expander vocabulary.
    """
    return [value] * n


def paired(**kwargs: list[Any]) -> Any:
    """Apply ``litmus_vectors`` with zip (paired) semantics across kwargs.

    Equivalent to ``@pytest.mark.litmus_vectors(**{"a,b": [(a1, b1), ...]})``
    — but typed, autocompleted, and validates dimensions match before
    pytest collection. All argvalue lists must be the same length.

    Example::

        from litmus import paired

        @paired(vin=[3, 4, 5], vout=[5, 7, 9])
        def test_rail(vin, vout): ...
        # 3 cases: (3, 5), (4, 7), (5, 9)

    Cross with an independent axis by stacking with a regular
    ``litmus_vectors`` decorator::

        @pytest.mark.litmus_vectors(temp=[25, 85])
        @paired(vin=[3, 4], vout=[5, 6])
        def test_rail(vin, vout, temp): ...
        # 2 (paired) × 2 (temp) = 4 cases
    """
    if not kwargs:
        raise ValueError("paired requires at least one keyword argument")
    lengths = {len(v) for v in kwargs.values()}
    if len(lengths) != 1:
        sizes = {name: len(v) for name, v in kwargs.items()}
        raise ValueError(f"paired requires all argvalues to have the same length; got {sizes}")
    argname_str = ",".join(kwargs)
    rows = [list(t) for t in zip(*kwargs.values(), strict=True)]
    return pytest.mark.litmus_vectors(**{argname_str: rows})
