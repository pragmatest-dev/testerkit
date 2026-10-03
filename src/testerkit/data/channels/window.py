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

from testerkit.data._catalog_keys import CHANNEL_SAMPLE_KEY


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


def dedup_on_sample_offset(table: pa.Table) -> pa.Table:
    """Collapse duplicate samples on the composite identity
    :data:`testerkit.data._catalog_keys.CHANNEL_SAMPLE_KEY`.

    The single shared implementation of the cross-consumer sample-dedup rule
    (docs/42 §3.2): both the local :meth:`~testerkit.data.channels.index.ChannelIndex.query`
    (union of the durable index + the live overlay, which can place the same
    sample in both) and the cloud's `channels_backend.windowed_series` (a
    sample landing in two overlapping forwarded segments) call this exact
    function so a sample is surfaced ONCE regardless of storage-layer overlap.

    `channel_id` is not read here — every caller already scopes its input to
    one channel before calling (a `WHERE channel_id = ?` / a `channel_id`-
    filtered segment set), so the columns actually compared are the
    remainder of the grain: `(session_id, sample_offset)`. Keeps the FIRST
    occurrence of each `(session_id, sample_offset)` pair — the table must
    already be ordered the way the caller wants ties broken (both callers
    order by `received_at` ascending before calling this). Rows with an
    unstamped `sample_offset` (`None` or negative — legacy, pre-cursor data)
    are never collapsed: each is always kept, since a negative/absent offset
    doesn't reliably identify a sample. A no-op when the table is empty or
    has no `sample_offset` column at all (an even-older segment shape).
    """
    _, session_col, offset_col = CHANNEL_SAMPLE_KEY
    if table.num_rows == 0 or offset_col not in table.column_names:
        return table
    sessions = table.column(session_col).to_pylist()
    offsets = table.column(offset_col).to_pylist()
    seen: set[tuple[Any, int]] = set()
    keep: list[int] = []
    for i, (s, o) in enumerate(zip(sessions, offsets, strict=True)):
        if o is not None and o >= 0:
            key = (s, o)
            if key in seen:
                continue
            seen.add(key)
        keep.append(i)
    return table if len(keep) == table.num_rows else table.take(keep)


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
