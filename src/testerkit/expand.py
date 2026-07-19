"""Inline list-builders for ``testerkit_sweeps`` and other list positions.

These are the Python-callable counterparts to the YAML range expanders
(``{linspace: [start, stop, num]}`` etc.). YAML can't call functions,
so YAML uses dict-with-known-key shape; inline Python calls these
helpers directly so users get IDE autocomplete + signature help::

    from testerkit import linspace

    @pytest.mark.testerkit_sweeps([
        {"vin": linspace(3.3, 5.5, 11)},
    ])
    def test_x(vin): ...

Each function delegates to the same underlying primitive the YAML
expander uses, so behavior is identical across surfaces.

For zipped (paired) axes, use a multi-key dict — the keys advance
together with one value-list each::

    @pytest.mark.testerkit_sweeps([
        {"vin": [3.3, 5.0], "vout": [3.30, 3.30]},
    ])
    def test_x(vin, vout): ...
"""

from __future__ import annotations

from typing import TypeVar

import numpy as np

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
