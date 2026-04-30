"""Runner-neutral verify primitive.

The actual ``verify(name, value)`` callable is built in
:mod:`litmus.execution._state`/:mod:`logger` and exposed through
each runner's native fixture/decorator surface. This module owns the
runner-agnostic pieces every runner needs:

* :class:`LimitFailure` — raised on FAIL, subclasses ``AssertionError``
* :func:`_apply_outcome` — stamps PASS / FAIL / DONE on a measurement
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
from litmus.models.test_config import Limit


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
        limit: Limit | None = ...,
        characteristic: str | None = ...,
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


def _apply_outcome(measurement: Measurement, limit: Limit | None, value: float | None) -> None:
    """Stamp ``measurement.outcome`` based on ``value`` against ``limit``.

    No limit → DONE. Limit + pass → PASS. Limit + fail → FAIL. Caller is
    responsible for raising on FAIL.
    """
    if limit is None or value is None:
        measurement.outcome = Outcome.DONE
        return
    measurement.outcome = Outcome.PASS if value in limit else Outcome.FAIL


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
        limit: Limit | None = None,
        characteristic: str | None = None,
    ) -> Measurement:
        logger = get_current_logger()
        if logger is None:
            raise RuntimeError(
                "verify() called without an active Litmus logger — "
                "is a Litmus runner plugin installed?"
            )

        # Explicit ``characteristic=`` bypasses the active-char ContextVar
        # while we resolve the limit + emit the row, then restores prior
        # state. This lets test code stamp a specific char_id even when
        # no for_characteristic block is in scope.
        if characteristic is not None:
            from litmus.execution._state import pushed_active_characteristic

            with pushed_active_characteristic(characteristic):
                measurement = logger.measure(name, value, limit=limit)
        else:
            measurement = logger.measure(name, value, limit=limit)

        # The measurement row carries whichever limit logger.measure
        # resolved. Reconstruct it so we can evaluate + raise.
        effective_limit = _reconstruct_limit_from_measurement(measurement)
        if effective_limit is None:
            effective_limit = _resolve_measurement_limit(
                name,
                inline_any=False,
                low=None,
                high=None,
                nominal=None,
                comparator=None,
                limit=limit,
                units=None,
            )

        _apply_outcome(measurement, effective_limit, measurement.value)

        if measurement.outcome == Outcome.FAIL:
            assert effective_limit is not None
            raise LimitFailure(
                name=name,
                value=measurement.value,
                limit=effective_limit,
                dut_pin=measurement.dut_pin,
                spec_ref=measurement.spec_ref,
            )
        return measurement

    return _verify


def _reconstruct_limit_from_measurement(m: Measurement) -> Limit | None:
    """Rebuild a ``Limit`` from the fields a logger stamped on a row.

    Returns ``None`` if no limit fields were set — i.e. the measurement
    was recorded in characterization mode.
    """
    if m.limit_low is None and m.limit_high is None and m.limit_nominal is None:
        return None
    from litmus.models.enums import Comparator

    cmp = Comparator(m.limit_comparator) if m.limit_comparator else Comparator.GELE
    return Limit(
        low=m.limit_low,
        high=m.limit_high,
        nominal=m.limit_nominal,
        units=m.units or "",
        characteristic_id=m.characteristic_id,
        spec_ref=m.spec_ref,
        comparator=cmp,
    )
