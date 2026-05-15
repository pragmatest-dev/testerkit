"""Pytest marker translation for Litmus :class:`TestEntry` fields.

The cascade-merged :class:`TestEntry` carries every Litmus-marker
field as a typed Pydantic value. This module turns those fields into
``pytest.mark.litmus_<name>(...)`` calls and attaches them to the
test item, plus translates any opaque ``runner.markers`` entries
(``flaky`` / ``skip`` / etc.) the user wrote in YAML or via the
profile cascade.

Also lives here:

* :func:`normalize_inline_list_payload` — accept either varargs or a
  single list arg from inline marker decorators; produce a canonical
  list of entries.
* :func:`enforce_no_inline_stacking` — raise a clear error when more
  than one ``litmus_X`` marker of the same name decorates a test.
* :func:`extract_characteristic_marker_ids` — pull the list of
  characteristic IDs out of a ``litmus_characteristics`` marker payload.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from litmus.models.test_config import TestEntry

LITMUS_MARKER_NAMES: tuple[str, ...] = (
    "litmus_limits",
    "litmus_sweeps",
    "litmus_mocks",
    "litmus_characteristics",
    "litmus_connections",
    "litmus_retry",
    "litmus_prompts",
)
"""Names of the Litmus markers that map 1:1 to ``TestEntry`` fields.

Used to enforce the no-stacking rule on inline decorators — at most
one of each per function — and to filter cascade-injected vs. inline
sources.
"""


class StackedMarkersError(ValueError):
    """Raised when a function carries more than one inline ``litmus_X`` marker.

    Multi-sweep goes in the single payload list; ``parametrize`` is
    the explicit exception (it's not in :data:`LITMUS_MARKER_NAMES`,
    so pytest's native stacking convention stays for it).
    """


def apply_entry_markers(item: pytest.Item, entry: TestEntry) -> None:
    """Attach pytest markers for every non-empty Litmus field on ``entry``.

    Emits one ``litmus_<name>`` marker per field, then walks
    ``entry.runner.markers`` for opaque ecosystem markers
    (``flaky`` / ``skip`` / etc.). ``parametrize`` entries are skipped
    here — they're consumed by :mod:`litmus.pytest_plugin.sweeps`.
    """
    if entry.limits:
        item.add_marker(pytest.mark.litmus_limits(**dict(entry.limits)))
    if entry.sweeps:
        item.add_marker(pytest.mark.litmus_sweeps([s.root for s in entry.sweeps]))
    if entry.mocks:
        item.add_marker(pytest.mark.litmus_mocks(list(entry.mocks)))
    if entry.characteristics:
        item.add_marker(pytest.mark.litmus_characteristics(list(entry.characteristics)))
    if entry.connections is not None:
        # ``entry.connections`` is the discriminated one-of: list[str]
        # binds by fixture-connection name (passed positionally to the
        # marker, matching ``litmus_characteristics([...])`` shape);
        # dict[str, Any] binds by instrument → channel (passed as
        # kwargs, matching ``litmus_limits(**by_name)`` shape).
        if isinstance(entry.connections, list):
            item.add_marker(pytest.mark.litmus_connections(entry.connections))
        else:
            item.add_marker(pytest.mark.litmus_connections(**entry.connections))
    if entry.retry is not None:
        item.add_marker(pytest.mark.litmus_retry(**entry.retry.model_dump(exclude_none=True)))
    if entry.prompts:
        item.add_marker(pytest.mark.litmus_prompts(**dict(entry.prompts)))

    for marker_entry in entry.runner.get("markers", []) or []:
        if not isinstance(marker_entry, dict) or len(marker_entry) != 1:
            raise ValueError(
                f"runner.markers entries must be single-key dicts; got {marker_entry!r}"
            )
        ((name, payload),) = marker_entry.items()
        if name == "parametrize":
            continue  # consumed by the runner's parametrize hook
        marker = getattr(pytest.mark, name)
        if isinstance(payload, dict):
            item.add_marker(marker(**payload))
        elif isinstance(payload, list):
            item.add_marker(marker(*payload))
        elif payload is None:
            item.add_marker(marker)
        else:
            item.add_marker(marker(payload))


def normalize_inline_list_payload(
    name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> list[Any]:
    """Normalize varargs-of-entries or single-list-arg into a canonical list.

    Used by inline ``@pytest.mark.litmus_sweeps`` / ``litmus_mocks`` /
    ``litmus_characteristics`` payload extraction. Sidecar / profile cascade
    produces the canonical list shape directly via Pydantic; inline
    accepts either a single list or pytest-style varargs (parametrize
    convention) for ergonomic stacking of single-entry markers.

    Entries may be raw dicts (from inline decorators) or already-typed
    Pydantic models (when the cascade injected the marker with typed
    instances). Caller validates per-entry shape against the right
    Pydantic model.
    """

    def _entry_ok(value: Any) -> bool:
        return isinstance(value, (dict, BaseModel))

    if kwargs:
        raise ValueError(
            f"{name} does not accept keyword arguments; pass a list of "
            "entries as one positional argument or varargs."
        )
    if not args:
        raise ValueError(f"{name} requires at least one entry.")
    if len(args) == 1 and isinstance(args[0], list):
        payload = args[0]
    elif all(_entry_ok(a) for a in args):
        payload = list(args)
    else:
        raise ValueError(
            f"{name} payload must be a single list or varargs of entries; got {args!r}"
        )
    for entry in payload:
        if not _entry_ok(entry):
            raise ValueError(f"{name} entries must be dicts or models; got {entry!r}")
    return list(payload)


def enforce_no_inline_stacking(marker_names: list[str]) -> None:
    """Raise :class:`StackedMarkersError` if any ``litmus_X`` name appears more than once.

    ``marker_names`` is the flat list of inline marker names attached
    to one test function (e.g. from pytest's ``item.own_markers``).
    Cascade-injected markers are not the caller's concern — Pydantic's
    dict-shaped ``limits:`` / ``prompts:`` and singleton ``connections:``
    / ``retry:`` enforce one-per-scope by construction; ``sweeps:`` /
    ``mocks:`` / ``specs:`` are list fields where stacking is
    intentional.
    """
    counts: dict[str, int] = {}
    for name in marker_names:
        if name in LITMUS_MARKER_NAMES:
            counts[name] = counts.get(name, 0) + 1
    duplicates = sorted(name for name, n in counts.items() if n > 1)
    if duplicates:
        raise StackedMarkersError(
            f"stacked Litmus markers not allowed: {duplicates}. "
            "Consolidate into one marker per type — multi-entry payloads (list of "
            "dicts for sweeps/mocks, multiple kwargs for limits/prompts) are "
            "supported on a single marker."
        )


def extract_characteristic_marker_ids(payloads: list[Any]) -> list[str]:
    """Extract characteristic IDs from a ``litmus_characteristics`` marker's payloads.

    ``payloads`` is a list of marker payloads; in practice it always
    has exactly one entry because :func:`enforce_no_inline_stacking`
    rejects multiple ``litmus_characteristics`` markers on the same
    test. The caller still passes a list for symmetry with the other
    marker extractors. Inline decorators may pass either varargs of
    strings or a single list arg; sidecar / profile cascade always
    passes a single list. Returns ``[]`` if no payload is present
    (marker absent / empty).
    """
    if not payloads:
        return []
    payload = payloads[0]

    payload_args: tuple[Any, ...] = payload if isinstance(payload, tuple) else (payload,)

    if not payload_args:
        raise ValueError("litmus_characteristics requires at least one characteristic ID.")
    if len(payload_args) == 1 and isinstance(payload_args[0], list):
        ids = payload_args[0]
    elif all(isinstance(a, str) for a in payload_args):
        ids = list(payload_args)
    else:
        raise ValueError(
            f"litmus_characteristics payload must be a list of characteristic ID strings "
            f"(or varargs of strings); got {payload_args!r}"
        )
    if not all(isinstance(i, str) for i in ids):
        raise ValueError(f"litmus_characteristics entries must be strings; got {ids!r}")
    return list(ids)
