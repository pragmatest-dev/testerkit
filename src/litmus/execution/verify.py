"""One-verb check-and-log contract for test bodies.

`verify(name, value)` is the primary verb a pytest-native Litmus test
calls. It writes a measurement row through the active logger (with full
auto-traceability), resolves a Limit via the usual chain, stamps
``outcome = PASS / FAIL / DONE``, and raises :class:`LimitFailure` on
FAIL so pytest marks the test as failed.

Characterization mode (no limit resolvable) records the row with
``outcome = DONE`` and does not raise — same source works before any
limit lands.

`limits[name]` gives read-only access to the resolved Limit for ad-hoc
pythonic assertions (``assert v in limits["vout"]``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import pytest

from litmus.config.test_config import Limit
from litmus.data.models import Measurement, Outcome


class VerifyFn(Protocol):
    """Signature of the ``verify`` fixture callable.

    Typing the fixture as this Protocol lets IDEs autocomplete
    ``verify("label", value, limit=...)`` instead of showing ``Any``.
    """

    def __call__(
        self,
        name: str,
        value: float | int | None,
        limit: Limit | None = ...,
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
        from litmus.execution._state import get_active_test_characteristic
        from litmus.execution.sidecar import resolve_limit

        cfg = self._configs[key]
        resolved = resolve_limit(cfg, test_char=get_active_test_characteristic())
        if resolved is None:
            raise KeyError(key)
        return resolved

    def __iter__(self):
        return iter(self._configs)

    def __len__(self) -> int:
        return len(self._configs)

    def __contains__(self, key: object) -> bool:
        return key in self._configs


@pytest.fixture
def verify() -> VerifyFn:
    """Callable fixture: ``verify(name, value[, limit=])`` — log + assert.

    Log unconditionally via the active logger, resolve a Limit from the
    chain, stamp the outcome, raise :class:`LimitFailure` on FAIL.
    """
    from litmus.execution.decorators import get_current_logger
    from litmus.execution.logger import _resolve_measurement_limit

    def _verify(name: str, value: float | int | None, limit: Limit | None = None) -> Measurement:
        logger = get_current_logger()
        if logger is None:
            raise RuntimeError(
                "verify() called without an active Litmus logger — "
                "is the pytest-native plugin installed?"
            )

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
    if m.low_limit is None and m.high_limit is None and m.nominal is None:
        return None
    from litmus.config.enums import Comparator

    cmp = Comparator(m.comparator) if m.comparator else Comparator.GELE
    return Limit(
        low=m.low_limit,
        high=m.high_limit,
        nominal=m.nominal,
        units=m.units or "",
        spec_id=m.spec_id,
        spec_ref=m.spec_ref,
        comparator=cmp,
    )
