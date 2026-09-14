"""Shared channel windowed-read transforms — decode + decimate.

The catalog-family (Arrow/Flight side) counterpart of ``run_projection`` for the
fact-table family: the shape-faithful transforms applied when reading a channel
window, single-sourced so the local :class:`~testerkit.data.channels.index.ChannelIndex`
and the cloud serving tier decode and decimate a channel window *identically*. A
bench and the cloud can never return a differently-shaped window from the same
segments.

Pure and dependency-light: no DuckDB, no I/O. The heavy decimation deps
(``numpy`` / ``tsdownsample``) are imported lazily, only on the ``max_points``
path, exactly as they were when these lived in ``index.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pyarrow as pa


def lttb_indices(values: Sequence[float], n_out: int) -> list[int]:
    """Largest Triangle Three Buckets downsampling — return selected indices.

    Visually lossless: preserves peaks, valleys, and shape better than naive
    stride decimation. Delegates to ``tsdownsample`` (compiled LTTB); first and
    last points are always kept.

    Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
    Representation", MSc thesis, University of Iceland, 2013.
    """
    n = len(values)
    if n <= n_out or n_out < 3:
        return list(range(n))
    # Heavy deps deferred off the module import path — only the decimation
    # (query w/ max_points) path pays numpy/tsdownsample's load.
    import numpy as np  # noqa: PLC0415
    from tsdownsample import LTTBDownsampler  # noqa: PLC0415

    indices = LTTBDownsampler().downsample(np.asarray(values, dtype=float), n_out=n_out)
    return [int(i) for i in indices]


def decimate_table(table: pa.Table, max_points: int) -> pa.Table:
    """Apply LTTB decimation to an Arrow table.

    Uses the ``value`` column for scalar channels, or row index for
    struct/array channels (where there's no single numeric column).
    """
    n = len(table)
    if n <= max_points:
        return table

    # Find best column for LTTB area calculation
    if "value" in table.schema.names:
        col = table.column("value")
        try:
            values: Sequence[float] = [float(v.as_py()) for v in col]
        except (TypeError, ValueError):
            # Non-numeric value column — fall back to stride
            indices = list(range(0, n, max(1, n // max_points)))[:max_points]
            return table.take(indices)
    else:
        # Struct/array channel — use row index as proxy (preserves time density)
        values = list(range(n))

    indices = lttb_indices(values, max_points)
    return table.take(indices)


def decode_value_column(table: pa.Table) -> pa.Table:
    """JSON-decode the VARCHAR ``value`` column back to typed values.

    Inverse of ``encode_value``: non-JSON strings pass through (matches
    ``batch_row_to_sample``). Values within one channel are homogeneous, so
    Arrow infers a single column type.
    """
    if "value" not in table.column_names or table.num_rows == 0:
        return table
    decoded: list[Any] = []
    for v in table.column("value").to_pylist():
        if v is None:
            decoded.append(None)
            continue
        try:
            decoded.append(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            decoded.append(v)
    idx = table.column_names.index("value")
    return table.set_column(idx, "value", pa.array(decoded))
