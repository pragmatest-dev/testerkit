"""Class-container instrument reservation.

A pytest class that hoists an instrument to class scope
(``@pytest.mark.usefixtures``) holds that instrument for the lifetime of the
class *container step* — not just per method. The per-method reserve is
reentrant, so the file lock never drops in the gap between two methods of the
same class. Method-scoped fixtures (requested as a method parameter) keep their
per-step grain.

These tests drive the container helpers in the pytest plugin directly against a
real file-lock :class:`InstrumentPool`, and prove the cross-method hold by
showing a second pool cannot reserve the role until the container closes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from litmus.execution import _state
from litmus.execution.run_scope import RunScope
from litmus.instruments.locks import ResourceInUse
from litmus.instruments.pool import InstrumentPool
from litmus.models.instrument import InstrumentRecord
from litmus.pytest_plugin import hooks


@pytest.mark.usefixtures("dmm")
class _ClassHoldsDmm:
    def test_a(self): ...
    def test_b(self): ...


@pytest.mark.usefixtures("psu", "dmm")
class _ClassHoldsTwo:
    def test_a(self): ...


@pytest.mark.usefixtures("not_a_role")
class _ClassHoldsUnknown:
    def test_a(self): ...


class _ClassHoldsNothing:
    def test_a(self): ...


def _real_record(role: str = "dmm", resource: str = "GPIB::16::INSTR") -> InstrumentRecord:
    return InstrumentRecord(role=role, instrument_id=f"{role}-001", resource=resource, mocked=False)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Lock files to a temp dir; reset plugin state between tests."""
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))
    # The container helper resolves outer-dim sweep values off the item stash;
    # these tests carry no sweeps, so short-circuit to "unswept".
    monkeypatch.setattr(hooks, "_outer_values_for", lambda item: ())
    yield
    _state.set_instrument_pool(None)
    _state.set_registered_instrument_roles(frozenset())


def _item(cls) -> Any:
    """Minimal stand-in for a pytest ``Item`` — only the attrs the container
    helper reads (``cls``, ``execution_count``, ``callspec``)."""
    return SimpleNamespace(cls=cls, execution_count=1, callspec=None)


class TestClassScopedRoleDetection:
    def test_single_class_fixture(self):
        _state.set_registered_instrument_roles(frozenset({"dmm", "psu"}))
        assert hooks._class_scoped_instrument_roles(_ClassHoldsDmm) == ["dmm"]

    def test_multiple_sorted(self):
        _state.set_registered_instrument_roles(frozenset({"dmm", "psu"}))
        assert hooks._class_scoped_instrument_roles(_ClassHoldsTwo) == ["dmm", "psu"]

    def test_unknown_role_excluded(self):
        _state.set_registered_instrument_roles(frozenset({"dmm", "psu"}))
        assert hooks._class_scoped_instrument_roles(_ClassHoldsUnknown) == []

    def test_no_class_fixture(self):
        _state.set_registered_instrument_roles(frozenset({"dmm", "psu"}))
        assert hooks._class_scoped_instrument_roles(_ClassHoldsNothing) == []

    def test_none(self):
        assert hooks._class_scoped_instrument_roles(None) == []


class TestContainerHold:
    def _setup_pool(self) -> tuple[RunScope, InstrumentPool]:
        logger = RunScope(uut_serial="SN1", station_id="st1")
        _state.set_registered_instrument_roles(frozenset({"dmm", "psu"}))
        pool = InstrumentPool(
            session_id=logger.test_run.session_id, event_log=None, channel_store=None
        )
        pool._records["dmm"] = _real_record()
        _state.set_instrument_pool(pool)
        return logger, pool

    @staticmethod
    def _contender() -> InstrumentPool:
        p = InstrumentPool(session_id=uuid4(), event_log=None, channel_store=None)
        p._records["dmm"] = _real_record()
        return p

    def test_hold_spans_methods(self):
        logger, pool = self._setup_pool()
        contender = self._contender()

        # Method A enters → container opens → dmm reserved for the sequence.
        hooks._ensure_class_container(logger, _item(_ClassHoldsDmm))
        with pytest.raises(ResourceInUse):
            contender.reserve("dmm", timeout=0)

        # Method A's own per-step reserve/release is a reentrant increment;
        # releasing it must NOT drop the container's outer hold.
        pool.reserve("dmm", step_index=0, step_retry=0)
        pool.release_reservation("dmm", step_index=0, step_retry=0)
        with pytest.raises(ResourceInUse):
            contender.reserve("dmm", timeout=0)

        # Method B of the SAME class → container stays open, still held.
        hooks._ensure_class_container(logger, _item(_ClassHoldsDmm))
        with pytest.raises(ResourceInUse):
            contender.reserve("dmm", timeout=0)

        # Container closes → hold released → contender can now reserve.
        hooks._close_open_class_container(logger)
        contender.reserve("dmm", timeout=0)
        contender.release_reservation("dmm")

    def test_transition_to_new_class_releases_prior(self):
        logger, _ = self._setup_pool()
        contender = self._contender()

        hooks._ensure_class_container(logger, _item(_ClassHoldsDmm))
        with pytest.raises(ResourceInUse):
            contender.reserve("dmm", timeout=0)

        # A different class with no dmm fixture → prior container closes and
        # releases before the new one opens.
        hooks._ensure_class_container(logger, _item(_ClassHoldsNothing))
        contender.reserve("dmm", timeout=0)
        contender.release_reservation("dmm")

        hooks._close_open_class_container(logger)

    def test_no_class_fixture_takes_no_hold(self):
        logger, _ = self._setup_pool()
        contender = self._contender()

        # Class declares no instrument at class scope → container takes no lock.
        hooks._ensure_class_container(logger, _item(_ClassHoldsNothing))
        contender.reserve("dmm", timeout=0)
        contender.release_reservation("dmm")

        hooks._close_open_class_container(logger)
