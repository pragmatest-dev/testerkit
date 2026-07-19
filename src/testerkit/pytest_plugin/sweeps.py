"""Translate :class:`SweepEntry` → pytest ``parametrize`` call shape.

The :class:`SweepEntry` model itself is runner-neutral (lives in
:mod:`testerkit.models.test_config`) — it just says "run this test body
N times with these argument bindings." Every runner has that idea.

This module is the *pytest-specific* translator: it takes a SweepEntry
and emits ``(argnames, argvalues)`` tuples shaped for
:meth:`pytest.Metafunc.parametrize`. Other runners would ship their
own translator next door (``openhtf_plugin/sweeps.py``, etc.) emitting
their own multi-run primitive.

Two callers feed sweep data through here:

* ``TestEntry.sweeps`` — typed :class:`SweepEntry` instances from the
  sidecar / profile cascade.
* ``TestEntry.runner.markers`` — opaque ``parametrize`` entries (each a
  single-key dict) from the pytest runner's namespace.
"""

from __future__ import annotations

from typing import Any

from testerkit.models.test_config import SweepEntry, TestEntry


def sweep_to_parametrize_args(entry: SweepEntry) -> tuple[str, list[Any]]:
    """Translate one :class:`SweepEntry` into ``(argnames, argvalues)``.

    Single-key dict = one independent sweep. Multi-key dict = a zipped
    sweep (paired argvalues, single nesting level). Zip-dim coherence
    is already validated by Pydantic (see :class:`SweepEntry`'s
    after-validator); this is purely a structural translation into
    the ``argnames, argvalues`` shape pytest's ``metafunc.parametrize``
    expects (and other runners adapt to their own parametrize call).
    """
    group = entry.root
    argnames = list(group.keys())
    argvalues_lists = list(group.values())
    if len(argnames) == 1:
        return argnames[0], list(argvalues_lists[0])
    argname_str = ",".join(argnames)
    argvalues = [list(t) for t in zip(*argvalues_lists, strict=True)]
    return argname_str, argvalues


def runner_marker_parametrize_calls(
    runner: dict[str, Any],
) -> list[tuple[Any, list[Any], dict[str, Any]]]:
    """Extract ``parametrize`` entries from a merged ``runner.markers`` list.

    Runner markers are single-key dicts; for ``parametrize`` the
    payload is either ``[argnames, argvalues]`` or
    ``{argnames, argvalues, ...}``. Returns
    ``(argnames, argvalues, extra_kwargs)`` triples in declared order.
    """
    out: list[tuple[Any, list[Any], dict[str, Any]]] = []
    for entry in runner.get("markers", []) or []:
        if not isinstance(entry, dict) or len(entry) != 1:
            continue
        ((name, payload),) = entry.items()
        if name != "parametrize":
            continue
        if isinstance(payload, list) and len(payload) >= 2:
            out.append((payload[0], list(payload[1]), {}))
        elif isinstance(payload, dict):
            argnames = payload.get("argnames")
            argvalues = payload.get("argvalues")
            extra = {k: v for k, v in payload.items() if k not in ("argnames", "argvalues")}
            if argnames is not None and argvalues is not None:
                out.append((argnames, list(argvalues), extra))
    return out


def parametrize_calls_for_entry(
    entry: TestEntry,
) -> list[tuple[Any, list[Any], dict[str, Any]]]:
    """Yield all ``(argnames, argvalues, extra)`` triples a TestEntry implies.

    Concatenates ``entry.sweeps`` (translated via :func:`sweep_to_parametrize_args`)
    with ``entry.runner.markers`` parametrize entries (via
    :func:`runner_marker_parametrize_calls`). Order matches the merge
    order — sweeps first, runner.markers last.
    """
    out: list[tuple[Any, list[Any], dict[str, Any]]] = []
    for sweep in entry.sweeps:
        argnames, argvalues = sweep_to_parametrize_args(sweep)
        out.append((argnames, argvalues, {}))
    out.extend(runner_marker_parametrize_calls(entry.runner))
    return out


def parametrize_call_rows(argnames: Any, argvalues: list[Any]) -> list[dict[str, Any]]:
    """Convert one parametrize call into a list of row dicts.

    Used to cross-product multiple parametrize calls when the runner
    needs to materialize the full Cartesian matrix (e.g. pytest's
    self-loop ``vectors`` fixture). Skips per-case ``id`` / ``marks``.
    """
    names = (
        [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
    )
    rows: list[dict[str, Any]] = []
    for raw in argvalues:
        values = getattr(raw, "values", None)
        if values is None:
            values = raw
        if len(names) == 1:
            rows.append({names[0]: values})
        else:
            if not isinstance(values, (tuple, list)):
                raise ValueError(
                    f"parametrize {argnames!r} expected a tuple per row; got {values!r}"
                )
            rows.append(dict(zip(names, values, strict=True)))
    return rows
