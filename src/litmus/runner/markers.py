"""Runner-neutral marker logic — translate :class:`TestEntry` to marker specs.

A :class:`TestEntry` is the merged sidecar + profile + cascade for one
test. Each runner needs to attach equivalent markers/decorators to the
test node it owns. This module produces a runner-neutral list of
:class:`MarkerSpec` instances; each runner's plugin loops the specs
and calls its host's marker primitive (``pytest.mark.X(...)`` /
``htf.measure(...)`` / etc).

Also lives here:

* :func:`normalize_inline_list_payload` — accept either varargs or a
  single list arg from inline marker decorators; produce a canonical
  list of entries (each is a Pydantic model or a raw dict the caller
  validates).
* :func:`enforce_no_inline_stacking` — given a count of how many
  inline ``litmus_X`` markers a test carries, raise a clear error if
  any type stacks more than once.

These functions take Pydantic models and primitive values; they don't
import pytest. Runners adapt their host's marker shape to the
:class:`MarkerSpec` interface and back.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from litmus.config.test_config import TestEntry

LITMUS_MARKER_NAMES: tuple[str, ...] = (
    "litmus_limits",
    "litmus_sweeps",
    "litmus_mocks",
    "litmus_specs",
    "litmus_connections",
    "litmus_retry",
    "litmus_prompts",
)
"""Names of the Litmus markers that map 1:1 to ``TestEntry`` fields.

Used to enforce the no-stacking rule on inline decorators — at most
one of each per function — and to filter cascade-injected vs. inline
sources.
"""


@dataclass
class MarkerSpec:
    """A runner-neutral marker description.

    The runner's plugin maps ``name`` to its host's marker primitive
    and applies ``args`` / ``kwargs`` to it. For pytest:
    ``pytest.mark.<name>(*args, **kwargs)``. For OpenHTF: name maps
    to a phase decorator. For unittest: skip / expectedFailure / etc.
    """

    name: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


class StackedMarkersError(ValueError):
    """Raised when a function carries more than one inline ``litmus_X`` marker.

    Multi-sweep goes in the single payload list; ``parametrize`` is
    the explicit exception (it's not in :data:`LITMUS_MARKER_NAMES`,
    so pytest's native stacking convention stays for it).
    """


def entry_to_marker_specs(entry: TestEntry) -> Iterator[MarkerSpec]:
    """Yield runner-neutral :class:`MarkerSpec` instances for a TestEntry.

    Emits one ``litmus_X`` spec per non-empty Litmus field, then one
    spec per ``runner.markers`` entry. ``parametrize`` entries under
    ``runner.markers`` are skipped here — they're consumed by the
    runner's parametrize-equivalent, see :mod:`litmus.runner.sweeps`.
    """
    if entry.limits:
        yield MarkerSpec("litmus_limits", kwargs=dict(entry.limits))
    if entry.sweeps:
        yield MarkerSpec("litmus_sweeps", args=([s.root for s in entry.sweeps],))
    if entry.mocks:
        yield MarkerSpec("litmus_mocks", args=(list(entry.mocks),))
    if entry.specs:
        yield MarkerSpec("litmus_specs", args=(list(entry.specs),))
    if entry.connections is not None:
        yield MarkerSpec(
            "litmus_connections",
            kwargs=entry.connections.model_dump(exclude_none=True),
        )
    if entry.retry is not None:
        yield MarkerSpec(
            "litmus_retry",
            kwargs=entry.retry.model_dump(exclude_none=True),
        )
    if entry.prompts:
        yield MarkerSpec("litmus_prompts", kwargs=dict(entry.prompts))

    for marker_entry in entry.runner.get("markers", []) or []:
        if not isinstance(marker_entry, dict) or len(marker_entry) != 1:
            raise ValueError(
                f"runner.markers entries must be single-key dicts; got {marker_entry!r}"
            )
        ((name, payload),) = marker_entry.items()
        if name == "parametrize":
            continue  # consumed by the runner's parametrize hook
        if isinstance(payload, dict):
            yield MarkerSpec(name, kwargs=dict(payload))
        elif isinstance(payload, list):
            yield MarkerSpec(name, args=tuple(payload))
        elif payload is None:
            yield MarkerSpec(name)
        else:
            yield MarkerSpec(name, args=(payload,))


def normalize_inline_list_payload(
    name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> list[Any]:
    """Normalize varargs-of-entries or single-list-arg into a canonical list.

    Used by inline ``@pytest.mark.litmus_sweeps`` / ``litmus_mocks`` /
    ``litmus_specs`` payload extraction. Sidecar / profile cascade
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
    to one test function (e.g. from pytest's ``item.own_markers`` or
    OpenHTF's per-phase decorator list). Cascade-injected markers
    are not the caller's concern — Pydantic's dict-shaped ``limits:``
    /  ``prompts:`` and singleton ``connections:`` / ``retry:`` enforce
    one-per-scope by construction; ``sweeps:`` / ``mocks:`` / ``specs:``
    are list fields where stacking is intentional.
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


def extract_specs_characteristic(specs_payloads: list[Any]) -> str | None:
    """Extract the single characteristic ID from a ``litmus_specs`` marker's payloads.

    ``specs_payloads`` is the ordered list of payloads each
    ``litmus_specs`` marker on the function carried (caller passes the
    most-specific marker first). Inline decorators may pass either
    varargs of strings or a single list arg; sidecar / profile
    cascade always passes a single list. v1 enforces cardinality 1 —
    multiple characteristic bindings raise ``ValueError`` (single
    iteration scope only). Returns ``None`` if no payload is present.
    """
    if not specs_payloads:
        return None
    payload = specs_payloads[0]

    if isinstance(payload, tuple):
        payload_args: tuple[Any, ...] = payload
    elif isinstance(payload, list):
        payload_args = (payload,)
    else:
        payload_args = (payload,)

    if not payload_args:
        raise ValueError("litmus_specs requires at least one characteristic ID.")
    if len(payload_args) == 1 and isinstance(payload_args[0], list):
        ids = payload_args[0]
    elif all(isinstance(a, str) for a in payload_args):
        ids = list(payload_args)
    else:
        raise ValueError(
            f"litmus_specs payload must be a list of characteristic ID strings "
            f"(or varargs of strings); got {payload_args!r}"
        )
    if not all(isinstance(i, str) for i in ids):
        raise ValueError(f"litmus_specs entries must be strings; got {ids!r}")
    if len(ids) != 1:
        raise ValueError(
            f"litmus_specs supports exactly one characteristic ID per test "
            f"(single iteration scope); got {len(ids)}: {ids!r}"
        )
    return ids[0]
