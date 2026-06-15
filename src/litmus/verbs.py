"""Top-level verb functions — ``observe``, ``verify``, ``measure``, ``stream``.

The four test-author intent verbs are exposed three ways. Pick the
one that matches how your code is structured:

1. **Top-level imports** (recommended — works from any code, not
   only tests)::

        from litmus import observe, verify, measure, stream

        def my_step(dmm, psu, voltage):
            psu.set_voltage(voltage)
            observe("psu.voltage", voltage)
            verify("rail_v", dmm.measure_voltage(), Limit(low=4.75, high=5.25))
            measure("rail_ripple", ripple(dmm.read_waveform()))  # record-only

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
:func:`litmus.execution._state.get_current_context`. The pytest
``context`` fixture pushes a Context onto that ContextVar; calling
a verb outside a pytest test (notebooks, scripts, custom UIs)
raises ``RuntimeError``.

**Interactive ``connect()`` mode does NOT populate the active
Context.** ``StationConnection.start`` wires the channel store and
event store ContextVars so that :func:`litmus.channels.write` /
:func:`litmus.channels.stream` / :func:`litmus.files.write` /
:func:`litmus.files.stream` work — but it deliberately does not
push a Context, because there is no test vector to anchor
observations and verifications to in interactive mode. Reach for
the store-direct surfaces (``channels.*`` / ``files.*``) in
notebooks, the operator UI, and bringup scripts. The test-author
verbs are test-author verbs.
"""

from __future__ import annotations

from typing import Any

from litmus.execution._state import get_current_context


def _active_context() -> Any:
    """Return the active :class:`Context`, or raise with a useful hint."""
    ctx = get_current_context()
    if ctx is None:
        raise RuntimeError(
            "No active Litmus context. The top-level verbs (observe / "
            "verify / measure / stream) are test-author verbs — they require the "
            "pytest ``context`` fixture, which pushes a Context for the "
            "duration of the test. Outside a pytest test (notebooks, "
            "scripts, custom UIs), use the store-direct surfaces instead: "
            "``litmus.channels.write`` / ``litmus.channels.stream`` for "
            "channels, ``litmus.files.write`` / ``litmus.files.stream`` "
            "for artifacts. Those work inside a ``connect(...)`` block "
            "and don't require an active test context."
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


def measure(
    name: str,
    value: float | int | None,
    limit: Any = None,
    *,
    characteristic: str | None = None,
    namespace: str | None = None,
) -> Any:
    """Record a measurement without judging it (→ measurement row).

    The record-only sibling of ``verify`` — stamps one measurement row
    with ``Outcome.DONE`` and never raises on a missing limit. Use when
    a value should be captured but not pass/fail judged
    (characterization, diagnostics, logged context). Thin top-level
    pass-through to :meth:`litmus.execution.harness.Context.measure`.
    """
    return _active_context().measure(
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


__all__ = ["observe", "verify", "measure", "stream"]
