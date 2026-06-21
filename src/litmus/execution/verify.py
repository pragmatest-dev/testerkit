"""Runner-neutral verify primitive.

The actual ``verify(name, value)`` callable is built in
:mod:`litmus.execution._state`/:mod:`logger` and exposed through
each runner's native fixture/decorator surface. This module owns the
runner-agnostic pieces every runner needs:

* :class:`LimitFailure` — raised on FAIL, subclasses ``AssertionError``
* :class:`MissingLimitError` — raised when ``verify`` is called without
  a resolvable limit (use ``logger.measure`` for record-only)
* :func:`_compute_outcome` — pure function that returns the verify
  outcome for a value against a resolved limit
* :class:`_LimitsMapping` — read-only ``name → Limit`` view
* :class:`VerifyFn` / :data:`LimitsFn` — type signatures consumers can
  annotate against without importing the runner adapter

The pytest adapter wraps these in fixtures (see
:mod:`litmus.pytest_plugin`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from litmus.data.models import Measurement, Outcome
from litmus.models.test_config import Limit, coerce_limit


class VerifyFn(Protocol):
    """Signature of the ``verify`` fixture callable.

    Typing the fixture as this Protocol lets IDEs autocomplete
    ``verify("label", value, limit=..., characteristic=...)`` instead of
    showing ``Any``.

    **Verbs separate cleanly**: ``verify`` judges scalars; ``observe``
    handles all shapes (routing + URI). To capture an artifact AND
    judge a metric, use both: ``observe("scope.cap", wf)`` to land the
    capture in ChannelStore — the resulting URI is written to the
    outputs lane under the name ``scope.cap`` (role ``output``) — then
    ``verify("overshoot", overshoot(wf), Limit(...))`` to judge the
    scalar metric. The artifact is queryable via
    ``FieldRef.output("scope.cap")`` in a ``parametric`` or
    ``histogram`` call (EAV join on role + name) — by data presence,
    not a flat ``out_<name>`` column (design doc §7).
    """

    def __call__(
        self,
        name: str,
        value: float | int | None,
        limit: Limit | dict[str, Any] | None = ...,
        characteristic: str | None = ...,
        namespace: str | None = ...,
        unit: str | None = ...,
    ) -> Measurement: ...


class MeasureFn(Protocol):
    """Signature of the ``measure`` fixture callable.

    The record-only peer of :class:`VerifyFn` — records a value with
    :attr:`Outcome.DONE` and no pass/fail judgment. Same row primitive
    underneath as ``verify``; the only difference is that ``measure``
    never judges and never raises on a missing limit.
    """

    def __call__(
        self,
        name: str,
        value: float | int | None,
        limit: Limit | dict[str, Any] | None = ...,
        characteristic: str | None = ...,
        namespace: str | None = ...,
        unit: str | None = ...,
    ) -> Measurement: ...


LimitsFn = Mapping[str, Limit]
"""Type alias for the ``limits`` fixture — a read-only ``name → Limit`` map."""


class LimitFailure(AssertionError):
    """Raised when a ``verify`` measurement falls outside its limit.

    Subclasses :class:`AssertionError` so ``pytest.raises(AssertionError)``
    still matches. Exposes structured fields for downstream tooling.
    """

    def __init__(
        self,
        *,
        name: str,
        value: float | None,
        limit: Limit,
        uut_pin: str | None = None,
        spec_ref: str | None = None,
    ) -> None:
        self.name = name
        self.value = value
        self.limit = limit
        self.uut_pin = uut_pin
        self.spec_ref = spec_ref
        super().__init__(self._format())

    def _format(self) -> str:
        unit = f" {self.limit.unit}" if self.limit.unit else ""
        lines = [f"{self.name} = {self.value}{unit} fails {self.limit!r}"]
        trailer: list[str] = []
        if self.uut_pin:
            trailer.append(f"pin: {self.uut_pin}")
        if self.spec_ref:
            trailer.append(f"spec: {self.spec_ref}")
        if trailer:
            lines.append("  " + "    ".join(trailer))
        return "\n".join(lines)


class MissingLimitError(ValueError):
    """Raised when ``verify`` is invoked with no resolvable limit.

    ``verify`` is judgment-bearing — calling it without a limit is a
    config bug, not a recordable outcome. Test code that wants to
    record a value without judging it should call ``logger.measure``
    (which stamps :attr:`Outcome.DONE`).
    """


def _compute_outcome(value: float | None, limit: Limit) -> Outcome:
    """Compute the verify outcome for a value against a resolved limit.

    Pure function — returns the outcome instead of mutating a
    Measurement. Verify resolves the limit upfront, calls this to
    decide PASSED / FAILED / ERRORED, and passes the result to
    ``logger.measure(outcome=...)`` so the cascade and event fire
    once with the final value.

    ``None`` value → ERRORED (couldn't measure → can't judge).
    """
    if value is None:
        return Outcome.ERRORED
    return Outcome.PASSED if value in limit else Outcome.FAILED


class _LimitsMapping(Mapping[str, Limit]):
    """Read-only ``name → Limit`` view for the active test.

    ``limits[name]`` returns the resolved :class:`Limit` for ``name``,
    walking the same band-match-with-sibling-catch-all logic as
    ``logger.measure`` so ad-hoc assertions like
    ``assert v in limits["vout"]`` see the same band the measurement
    would. Raises ``KeyError`` when nothing is configured for ``name``,
    or when the resolved policy can't produce a Limit (characterization
    mode — record-only, no pass/fail check).
    """

    def __init__(self, configs: dict[str, Any]):
        self._configs = configs

    def __getitem__(self, key: str) -> Limit:
        from litmus.execution._state import get_active_characteristic
        from litmus.execution.sidecar import resolve_limit

        cfg = self._configs[key]
        resolved = resolve_limit(cfg, test_char=get_active_characteristic())
        if resolved is None:
            raise KeyError(key)
        return resolved

    def __iter__(self):
        return iter(self._configs)

    def __len__(self) -> int:
        return len(self._configs)

    def __contains__(self, key: object) -> bool:
        return key in self._configs


def _perform_verify(
    name: str,
    value: float | int | None,
    limit: Limit | dict[str, Any] | None = None,
    characteristic: str | None = None,
    namespace: str | None = None,
    unit: str | None = None,
) -> Measurement:
    """The actual verify implementation. Called by both
    :meth:`Context.verify` (the method form) and the bare ``verify``
    pytest fixture (the callable form). Resolves the active logger
    via the usual ContextVar chain — no runner-specific arguments
    needed.

    The two shapes — method on Context and bare callable — share
    this one body so the verb behaves identically regardless of which
    surface the test author reaches for. Symmetric with
    :meth:`Context.observe` / the bare ``observe`` fixture.

    **Verbs are separate**:

    * ``verify`` is **judgment-bearing** — it takes a scalar and a
      limit, decides PASSED / FAILED / ERRORED, and raises on FAIL.
      Non-scalar values raise :class:`TypeError` pointing at
      ``observe``.
    * ``observe`` handles **all shapes** — scalar / array / Waveform /
      blob. Non-scalars route to the right store; the resulting URI
      lands in the output lane under ``<name>`` for downstream
      query via ``role='output' AND name=<name>`` (design doc §7).

    To capture an artifact AND judge a metric in the same step::

        # URI lands in the outputs lane under name "scope.ch1.capture" (role "output")
        observe("scope.ch1.capture", wf)
        verify("overshoot", overshoot(wf), Limit(low=0, high=0.5))

    The artifact is queryable via ``FieldRef.output("scope.ch1.capture")``
    in ``parametric`` / ``histogram`` (EAV join on role + name) — by data
    presence, not a flat ``out_scope_ch1_capture`` column.
    """
    # Item 16: namespace= prefix sugar. The effective name (used for
    # limit lookup, measurement_name on the row, and the outputs lane
    # key) is "{namespace}.{name}". Pure opt-in.
    if namespace:
        name = f"{namespace}.{name}"
    from contextlib import nullcontext

    from litmus.execution._state import (
        get_current_run_scope,
        pushed_active_characteristic,
    )
    from litmus.execution.logger import _resolve_measurement_limit

    run_scope = get_current_run_scope()
    if run_scope is None:
        raise RuntimeError(
            "verify() called without an active Litmus run scope — "
            "is a Litmus runner plugin installed?"
        )

    # Verb-semantic guard: ``verify`` is judgment-bearing and operates
    # on numeric scalars. Non-scalar values belong to ``observe`` —
    # raise a clear error pointing the caller at the right verb rather
    # than silently routing the value to a store. Keeps the verbs
    # clean: observe = "stash this"; verify = "judge this number."
    if value is not None and not isinstance(value, (int, float)):
        raise TypeError(
            f"verify({name!r}, ...): expected a numeric scalar (int / "
            f"float / None) but got {type(value).__name__}. ``verify`` is "
            "judgment-bearing — it judges a scalar against a limit. To "
            "capture a non-scalar artifact, use ``observe(name, value)`` "
            "which routes by shape (Waveform / array → ChannelStore; "
            "bytes / Path → FileStore) and stamps the resulting URI in "
            "the active vector's outputs lane. To verify a metric of the "
            "artifact, extract a scalar first: "
            "``verify('overshoot', overshoot(wf), Limit(...))``."
        )

    # Accept dict literals at the call site (shared with ``logger.measure``).
    limit_obj = coerce_limit(limit)

    # Fail loud when unit= and Limit(unit=) conflict — identical to
    # the channels store's unit-conflict guard (fail-loud pattern).
    if unit is not None and limit_obj is not None and limit_obj.unit and limit_obj.unit != unit:
        raise ValueError(
            f"verify({name!r}): unit={unit!r} conflicts with "
            f"Limit(unit={limit_obj.unit!r}). Pass unit= to only one."
        )

    # Resolve limit + record under the same ``characteristic`` context
    # so the limit chain and auto-traceability both see the override.
    char_ctx = (
        pushed_active_characteristic(characteristic)
        if characteristic is not None
        else nullcontext()
    )

    with char_ctx:
        effective_limit = _resolve_measurement_limit(
            name,
            inline_any=False,
            low=None,
            high=None,
            nominal=None,
            comparator=None,
            limit=limit_obj,
            unit=None,
        )
        if effective_limit is None:
            # Characterization / record-only profile opt-in:
            # when the active profile sets ``verify_requires_limit: false``,
            # fall through to ``logger.measure`` semantics — record the
            # value with Outcome.DONE, no judgment. Default behavior
            # (no profile, or profile leaves verify_requires_limit unset,
            # or sets it to True) raises.
            from litmus.execution._state import get_active_profile

            profile = get_active_profile()
            if profile is not None and profile.verify_requires_limit is False:
                return run_scope.measure(name, value, limit=None, unit=unit)
            raise MissingLimitError(
                f"verify({name!r}, ...) has no limit to judge against. "
                "Pass limit=Limit(...), configure a limit via "
                "@pytest.mark.litmus_limits / sidecar / profile / part spec, "
                "use logger.measure() to record without judging, or "
                "set ``verify_requires_limit: false`` on the active profile."
            )
        outcome = _compute_outcome(
            float(value) if value is not None else None,
            effective_limit,
        )
        measurement = run_scope.measure(name, value, limit=limit_obj, outcome=outcome, unit=unit)

    if outcome == Outcome.FAILED:
        raise LimitFailure(
            name=name,
            value=measurement.value,
            limit=effective_limit,
            uut_pin=measurement.uut_pin,
            spec_ref=measurement.spec_ref,
        )
    return measurement


def build_verify_callable() -> VerifyFn:
    """Construct the runner-neutral ``verify`` callable.

    Each runner adapter wraps this with its native fixture/decorator
    primitive (e.g. ``@pytest.fixture`` in :mod:`litmus.pytest_plugin`).
    The callable delegates to :func:`_perform_verify` so the method
    form (``Context.verify``) and the bare-callable form share one
    implementation.
    """
    return _perform_verify


def _perform_measure(
    name: str,
    value: float | int | None,
    limit: Limit | dict[str, Any] | None = None,
    characteristic: str | None = None,
    namespace: str | None = None,
    unit: str | None = None,
) -> Measurement:
    """The actual measure implementation — ``verify`` minus the judgment.

    Records one measurement row with :attr:`Outcome.DONE` (the
    recorder semantic — "ran, no judgment") and never raises on a
    missing limit. Called by both :meth:`Context.measure` (the method
    form) and the bare ``measure`` pytest fixture (the callable form),
    so the verb behaves identically regardless of which surface the
    test author reaches for — symmetric with :func:`_perform_verify`.

    Resolves the active logger via the usual ContextVar chain, so it
    needs no harness reference and works in pytest-native tests and
    programmatic paths alike. The limit, when passed, is recorded on
    the row (so analysis sees the active band) but never evaluated —
    use :func:`_perform_verify` to judge.
    """
    # namespace= prefix sugar — same rule as verify / observe.
    if namespace:
        name = f"{namespace}.{name}"
    from contextlib import nullcontext

    from litmus.execution._state import (
        get_current_run_scope,
        pushed_active_characteristic,
    )

    run_scope = get_current_run_scope()
    if run_scope is None:
        raise RuntimeError(
            "measure() called without an active Litmus run scope — "
            "is a Litmus runner plugin installed?"
        )

    # Accept dict literals at the call site (shared with ``verify``).
    limit_obj = coerce_limit(limit)
    char_ctx = (
        pushed_active_characteristic(characteristic)
        if characteristic is not None
        else nullcontext()
    )
    with char_ctx:
        return run_scope.measure(name, value, limit=limit_obj, unit=unit)


def build_measure_callable() -> MeasureFn:
    """Construct the runner-neutral ``measure`` callable (record-only).

    The record-only peer of :func:`build_verify_callable`. Each runner
    adapter wraps this with its native fixture primitive. The callable
    delegates to :func:`_perform_measure` so the method form
    (``Context.measure``) and the bare-callable form share one body.
    """
    return _perform_measure
