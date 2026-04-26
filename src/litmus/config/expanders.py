"""YAML-load range expanders.

Any list-producing position in a Litmus YAML file accepts a single-key
dict whose key names a generator. The loader walks the tree and
replaces every such dict with the expanded list *before* Pydantic
validation — so schema shapes only ever see plain lists.

Supported generators::

    {linspace: [start, stop, num]}     -> numpy.linspace
    {arange:   [start, stop, step]}    -> numpy.arange  (stop exclusive)
    {logspace: [start, stop, num]}     -> numpy.logspace (base 10)
    {geomspace:[start, stop, num]}     -> numpy.geomspace
    {repeat:   [value, n]}             -> [value] * n
    {range:    [start, stop]}          -> list(range(...))
    {range:    [start, stop, step]}    -> list(range(...))

Applies generically: ``litmus_sweeps`` argvalues, ``zip_bands``
arrays, station/fixture channel enumerations, prompt delay sequences
— anywhere a list is expected.

For zipped axes in ``litmus_sweeps``: don't use a special zip
expander. Instead, put multiple keys in one sweep dict — multi-key
auto-zips with dim coherence enforced at decoration time. Each key
can independently use range expanders, and Litmus checks they
expand to equal lengths::

    - litmus_sweeps:
        - vin:  {linspace: [3.3, 5.5, 5]}    # expands to 5 floats
          vout: {linspace: [3.30, 3.32, 5]}  # expands to 5 floats
          # 5 zipped pairs; mismatched lengths would raise UsageError
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

_EXPANDERS: dict[str, Callable[[list[Any]], list[Any]]] = {
    "linspace": lambda args: np.linspace(*args).tolist(),
    "arange": lambda args: np.arange(*args).tolist(),
    "logspace": lambda args: np.logspace(*args).tolist(),
    "geomspace": lambda args: np.geomspace(*args).tolist(),
    "repeat": lambda args: [args[0]] * int(args[1]),
    "range": lambda args: list(range(*args)),
}


def expand_ranges(data: Any) -> Any:
    """Recursively replace ``{<expander>: [...]}`` nodes with expanded lists.

    The replacement is *in-place semantically* but returns a new object so
    the caller can reassign. Unknown dict shapes pass through unchanged;
    lists and nested dicts are walked.
    """
    if isinstance(data, dict):
        if len(data) == 1:
            (key, value) = next(iter(data.items()))
            if key in _EXPANDERS and isinstance(value, list):
                try:
                    return _EXPANDERS[key](value)
                except Exception as exc:
                    raise ValueError(
                        f"Range expander {key!r} failed on args {value!r}: {exc}"
                    ) from exc
        return {k: expand_ranges(v) for k, v in data.items()}
    if isinstance(data, list):
        return [expand_ranges(item) for item in data]
    return data
