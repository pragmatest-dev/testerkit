"""Shared helpers for ``EventSubscriber`` exporters.

Cross-format utilities for the per-format subscribers in this
package. Currently scoped to dynamic-column discovery — every
flat-row exporter (CSV, TDMS, HDF5) needs to walk a
``MeasurementRecorded`` sequence and collect the ordered set of
``inputs`` / ``outputs`` keys that appear anywhere in the run.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def discover_dynamic_columns(
    measurements: Iterable[Any],
) -> tuple[list[str], list[str]]:
    """Return ordered, deduplicated ``(in_keys, out_keys)`` across the events.

    Walks each event's ``inputs`` and ``outputs`` dicts in arrival
    order and records each new key the first time it's seen.
    Insertion order is preserved so deterministic output formats
    (CSV column order, TDMS channel order) stay stable across runs
    of the same test.
    """
    in_keys: list[str] = []
    out_keys: list[str] = []
    in_seen: set[str] = set()
    out_seen: set[str] = set()
    for m in measurements:
        for k in m.inputs:
            if k not in in_seen:
                in_seen.add(k)
                in_keys.append(k)
        for k in m.outputs:
            if k not in out_seen:
                out_seen.add(k)
                out_keys.append(k)
    return in_keys, out_keys
