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
    """

    def __call__(
        self,
        name: str,
        value: float | int | None,
        limit: Limit | dict[str, Any] | None = ...,
        characteristic: str | None = ...,
        namespace: str | None = ...,
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
        dut_pin: str | None = None,
        spec_ref: str | None = None,
    ) -> None:
        self.name = name
        self.value = value
        self.limit = limit
        self.dut_pin = dut_pin
        self.spec_ref = spec_ref
        super().__init__(self._format())

    def _format(self) -> str:
        units = f" {self.limit.units}" if self.limit.units else ""
        lines = [f"{self.name} = {self.value}{units} fails {self.limit!r}"]
        trailer: list[str] = []
        if self.dut_pin:
            trailer.append(f"pin: {self.dut_pin}")
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


def build_verify_callable() -> VerifyFn:
    """Construct the runner-neutral ``verify`` callable.

    Each runner adapter wraps this with its native fixture/decorator
    primitive (e.g. ``@pytest.fixture`` in :mod:`litmus.pytest_plugin`).
    The callable resolves the active logger via the usual ContextVar
    chain — no runner-specific arguments needed.
    """
    from litmus.execution._state import get_current_logger
    from litmus.execution.logger import _resolve_measurement_limit

    def _verify(
        name: str,
        value: float | int | None,
        limit: Limit | dict[str, Any] | None = None,
        characteristic: str | None = None,
        namespace: str | None = None,
    ) -> Measurement:
        # Item 16: namespace= prefix sugar. The effective name (used
        # for limit lookup, measurement_name on the row, and any
        # downstream out_<name> projection) is "{namespace}.{name}".
        # Pure opt-in convenience — nothing automatic.
        if namespace:
            name = f"{namespace}.{name}"
        from contextlib import nullcontext

        from litmus.execution._state import pushed_active_characteristic

        logger = get_current_logger()
        if logger is None:
            raise RuntimeError(
                "verify() called without an active Litmus logger — "
                "is a Litmus runner plugin installed?"
            )

        # Accept dict literals at the call site (shared with ``logger.measure``).
        limit_obj = coerce_limit(limit)

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
                units=None,
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
                    return logger.measure(name, value, limit=None)
                raise MissingLimitError(
                    f"verify({name!r}, ...) has no limit to judge against. "
                    "Pass limit=Limit(...), configure a limit via "
                    "@pytest.mark.litmus_limits / sidecar / profile / product spec, "
                    "use logger.measure() to record without judging, or "
                    "set ``verify_requires_limit: false`` on the active profile."
                )
            outcome = _compute_outcome(
                float(value) if value is not None else None,
                effective_limit,
            )
            measurement = logger.measure(name, value, limit=limit_obj, outcome=outcome)

        if outcome == Outcome.FAILED:
            raise LimitFailure(
                name=name,
                value=measurement.value,
                limit=effective_limit,
                dut_pin=measurement.dut_pin,
                spec_ref=measurement.spec_ref,
            )
        return measurement

    return _verify
