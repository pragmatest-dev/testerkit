"""Top-level verb functions — ``observe``, ``verify``, ``stream``.

The three test-author intent verbs are exposed three ways. Pick the
one that matches how your code is structured:

1. **Top-level imports** (recommended — works from any code, not
   only tests)::

        from litmus import observe, verify, stream

        def my_step(dmm, psu, voltage):
            psu.set_voltage(voltage)
            observe("psu.voltage", voltage)
            verify("rail_v", dmm.measure_voltage(), Limit(low=4.75, high=5.25))

2. **Pytest fixtures** (idiomatic when a test signature already
   takes other fixtures)::

        def test_rail(observe, verify, dmm, psu):
            ...

3. **Context methods** (programmatic / non-pytest paths)::

        with TestHarness("my-run") as harness:
            with harness.vector(...) as ctx:
                ctx.observe("...", value)
                ctx.verify("...", measured, limit)

All three shapes route through the same underlying
:class:`~litmus.execution.harness.Context` methods, so behavior is
identical regardless of which shape your code reaches for.

Resolution chain: the top-level verbs read the active context from
:func:`litmus.execution._state.get_current_context` (set by the
pytest ``context`` fixture and by
:meth:`~litmus.connect.StationConnection.start`). Calling a verb
outside an active context raises ``RuntimeError`` with a hint about
which fixture / connection initialiser populates the context.
"""

from __future__ import annotations

from typing import Any

from litmus.execution._state import get_current_context


def _active_context() -> Any:
    """Return the active :class:`Context`, or raise with a useful hint."""
    ctx = get_current_context()
    if ctx is None:
        raise RuntimeError(
            "No active Litmus context. Top-level verbs (observe / verify / "
            "stream) require an active Context, which is set by either the "
            "pytest ``context`` fixture or by ``connect(...)``'s "
            "``StationConnection`` enter. Use the verb's pytest fixture "
            "form, or wrap your code in ``with connect(...) as station: ...``."
        )
    return ctx


def observe(key: str, value: Any, *, namespace: str | None = None) -> None:
    """Record an observation (→ ``out_*`` column).

    Thin top-level pass-through to
    :meth:`litmus.execution.harness.Context.observe`. See that method
    for the full polymorphic dispatch rules (scalar / Waveform /
    numeric_array / blob / URI / sink-handle).
    """
    _active_context().observe(key, value, namespace=namespace)


def verify(
    name: str,
    value: float | int | None,
    limit: Any = None,
    *,
    characteristic: str | None = None,
    namespace: str | None = None,
) -> Any:
    """Record + judge a measurement (→ measurement row).

    Thin top-level pass-through to
    :meth:`litmus.execution.harness.Context.verify`. See that method
    for limit-resolution rules + ``MissingLimitError`` semantics.
    """
    return _active_context().verify(
        name, value, limit, characteristic=characteristic, namespace=namespace
    )


def stream(name: str, sample: Any, *, namespace: str | None = None) -> str:
    """Append one sample to a channel (→ ``channel://`` URI).

    Thin top-level pass-through to
    :meth:`litmus.execution.harness.Context.stream`. Strictly
    orthogonal to ``observe`` — never stamps ``out_*`` on the active
    vector; wire to a vector explicitly via ``observe(name, sink)`` if
    association is wanted.
    """
    return _active_context().stream(name, sample, namespace=namespace)


__all__ = ["observe", "verify", "stream"]
