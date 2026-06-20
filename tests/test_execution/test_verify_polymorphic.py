"""verify is scalar-only — non-scalars route via ``observe``.

Per the verb-semantic split (and ``MissingLimitError``'s long-standing
docstring): ``verify`` is judgment-bearing — it judges a numeric scalar
against a limit. Non-scalar values belong to ``observe``, which already
handles every shape (Waveform / numeric_array → ChannelStore;
bytes / Path / blob → FileStore) and stamps the resulting URI on the
active vector's ``out_<name>`` for query-by-presence
(``WHERE out_<name> IS NOT NULL``).

These tests pin the contract: ``verify`` raises ``TypeError`` on
non-scalars with a clear message pointing at the right verb.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from litmus.data.channels.store import ChannelStore
from litmus.data.models import Outcome, Waveform
from litmus.execution._state import (
    get_current_run_scope,
    push_current_context,
    reset_current_context,
    set_channel_store,
    set_current_run_scope,
)
from litmus.execution.harness import Context, TestHarness
from litmus.execution.logger import RunScope
from litmus.execution.verify import _perform_verify
from litmus.models.test_config import Limit


@pytest.fixture
def session(tmp_path: Path):
    """Real session wiring: logger + ChannelStore + EventLog + Context."""
    from litmus.data.event_log import EventLog
    from litmus.data.files import _reset_for_tests as _reset_filestore
    from litmus.data.files import store as fstore_module

    session_id = uuid4()
    run_id = uuid4()

    event_log = EventLog(log_dir=tmp_path / "events", session_id=session_id)

    cstore = ChannelStore(tmp_path, session_id, flush_threshold=1000, event_log=event_log)
    cstore.open()

    orig_resolve = fstore_module.resolve_data_dir
    fstore_module.resolve_data_dir = lambda _=None: tmp_path
    _reset_filestore()

    run_scope = RunScope(
        uut_serial="POC-UUT-001",
        station_id="poc-station",
        run_id=run_id,
        session_id=session_id,
        data_dir=tmp_path,
    )
    run_scope.event_log = event_log

    harness = TestHarness(session_id=session_id, channel_store=cstore, logger=run_scope)
    ctx = Context(harness=harness, channel_store=cstore, session_id=session_id)

    prior_run_scope = get_current_run_scope()
    set_current_run_scope(run_scope)
    set_channel_store(cstore)
    token = push_current_context(ctx)
    run_scope.start_step("test_step")

    class _Session:
        pass

    sess = _Session()
    sess.run_scope = run_scope  # type: ignore[attr-defined]
    sess.channel_store = cstore  # type: ignore[attr-defined]
    sess.ctx = ctx  # type: ignore[attr-defined]

    try:
        yield sess
    finally:
        run_scope.end_step()
        reset_current_context(token)
        set_current_run_scope(prior_run_scope)
        set_channel_store(None)
        cstore.close()
        event_log.close()
        fstore_module.resolve_data_dir = orig_resolve
        _reset_filestore()


# --------------------------------------------------------------------- #
# Non-scalar values raise — pointing at observe                          #
# --------------------------------------------------------------------- #


class TestVerifyRejectsNonScalars:
    """``verify`` is judgment-bearing; non-scalars belong to ``observe``."""

    @pytest.mark.parametrize(
        "value",
        [
            [1.0, 2.0, 3.0],
            np.array([0.1, 0.2, 0.3]),
            Waveform(Y=[0.0, 1.0, 2.0], dt=0.001),
            b"\x89PNG\r\n\x1a\nfake-bytes",
        ],
        ids=["list", "ndarray", "waveform", "bytes"],
    )
    def test_non_scalar_raises_pointing_at_observe(self, session: Any, value: Any) -> None:
        with pytest.raises(TypeError, match="judgment-bearing"):
            _perform_verify("artifact", value)  # type: ignore[arg-type]

    def test_path_raises_pointing_at_observe(self, session: Any, tmp_path: Path) -> None:
        src = tmp_path / "capture.tdms"
        src.write_bytes(b"x")
        with pytest.raises(TypeError, match="judgment-bearing"):
            _perform_verify("artifact", src)  # type: ignore[arg-type]

    def test_error_message_names_observe(self, session: Any) -> None:
        """The error message must point the caller at the right verb."""
        with pytest.raises(TypeError) as exc_info:
            _perform_verify("artifact", [1, 2, 3])  # type: ignore[arg-type]
        msg = str(exc_info.value)
        assert "observe(name, value)" in msg
        # And tell them how to verify a metric extracted from the artifact
        assert "overshoot" in msg or "extract a scalar" in msg

    def test_non_scalar_with_limit_still_raises_typeerror(self, session: Any) -> None:
        """Limit doesn't rescue non-scalars — it's still the wrong verb."""
        with pytest.raises(TypeError, match="judgment-bearing"):
            _perform_verify(
                "artifact",
                [1.0, 2.0, 3.0],  # type: ignore[arg-type]
                limit=Limit(low=0, high=10, unit="V"),
            )


# --------------------------------------------------------------------- #
# Scalar path unchanged (regression guard)                               #
# --------------------------------------------------------------------- #


class TestScalarPathStillWorks:
    def test_scalar_with_limit_judges_pass(self, session: Any) -> None:
        m = _perform_verify("vout", 3.3, limit=Limit(low=3.0, high=3.6, unit="V"))
        assert m.outcome == Outcome.PASSED
        assert m.value == 3.3

    def test_scalar_with_limit_raises_on_fail(self, session: Any) -> None:
        from litmus.execution.verify import LimitFailure

        with pytest.raises(LimitFailure):
            _perform_verify("vout", 12.0, limit=Limit(low=3.0, high=3.6, unit="V"))

    def test_scalar_without_limit_raises_missing_limit(self, session: Any) -> None:
        from litmus.execution.verify import MissingLimitError

        with pytest.raises(MissingLimitError):
            _perform_verify("vout", 3.3)

    def test_none_value_with_limit_errors_outcome(self, session: Any) -> None:
        """value=None (couldn't measure) is a recordable scalar outcome:
        ERRORED, per design (``_compute_outcome`` returns ERRORED on None)."""
        m = _perform_verify("vout", None, limit=Limit(low=3.0, high=3.6, unit="V"))
        assert m.outcome == Outcome.ERRORED


# --------------------------------------------------------------------- #
# The right pattern: observe + verify (in the same step)                 #
# --------------------------------------------------------------------- #


class TestObserveThenVerifyPattern:
    """The verb pair: ``observe`` stashes the artifact; ``verify`` judges
    the metric. The artifact's URI rides on the active vector's
    ``out_<name>`` so it's queryable from any row in the vector via
    ``WHERE out_<name> IS NOT NULL`` (design doc §7).
    """

    def test_observe_artifact_then_verify_metric_works(self, session: Any) -> None:
        wf = Waveform(Y=[0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0], dt=0.001)
        session.ctx.observe("scope.cap", wf)
        # peak metric extracted from the artifact, judged against a limit
        peak = max(wf.Y)
        m = _perform_verify("peak", peak, limit=Limit(low=0.0, high=5.0, unit="V"))
        assert m.outcome == Outcome.PASSED
        # The artifact URI is queryable from the vector via out_*
        assert session.ctx._observations.get("scope.cap", "").startswith("channel://scope.cap")


# --------------------------------------------------------------------- #
# unit= carries the engineering unit onto the measurement                #
# --------------------------------------------------------------------- #


class TestVerifyMeasureUnit:
    """``unit=`` stamps the measurement's engineering unit — singular (one
    quantity, one unit), symmetric with ``configure`` / ``observe``.
    Inline ``unit=`` is primary; the resolved limit supplies the default.
    """

    def test_verify_unit_sets_measurement_unit(self, session: Any) -> None:
        m = session.ctx.verify("vout", 3.3, {"low": 3.0, "high": 3.6, "unit": ""}, unit="V")
        assert m.outcome == Outcome.PASSED
        assert m.unit == "V"

    def test_verify_inline_unit_overrides_limit_unit(self, session: Any) -> None:
        m = session.ctx.verify("vout", 3.3, {"low": 3.0, "high": 3.6, "unit": "mV"}, unit="V")
        assert m.unit == "V"

    def test_verify_limit_supplies_default_unit(self, session: Any) -> None:
        m = session.ctx.verify("iout", 1.2, {"low": 0.0, "high": 2.0, "unit": "A"})
        assert m.unit == "A"

    def test_measure_unit_sets_measurement_unit(self, session: Any) -> None:
        m = session.ctx.measure("temp", 24.8, unit="degC")
        assert m.unit == "degC"


# --------------------------------------------------------------------- #
# observe unit= unifies with the channel (#45) — fail-loud like stream   #
# --------------------------------------------------------------------- #


class TestObserveChannelUnit:
    """A channel-routed ``observe`` unifies its unit with the channel: the
    unit lands on the channel descriptor (set-once, immutable per session)
    and the observation lane defaults FROM the channel when unit= is omitted.
    A contradicting unit fails loud, same as ``stream``.
    """

    def test_observe_unit_lands_on_channel_and_lane(self, session: Any) -> None:
        session.ctx.observe("scope.v", Waveform(Y=[1.0, 2.0, 3.0], dt=0.001), unit="V")
        assert session.channel_store.channel_unit("scope.v") == "V"
        assert session.ctx._observation_units["scope.v"] == "V"

    def test_observe_unit_from_waveform_attributes(self, session: Any) -> None:
        wf = Waveform(Y=[1.0, 2.0], dt=0.001, attributes={"unit": "A"})
        session.ctx.observe("scope.i", wf)  # no explicit unit → attributes win
        assert session.channel_store.channel_unit("scope.i") == "A"
        assert session.ctx._observation_units["scope.i"] == "A"

    def test_observe_lane_defaults_from_existing_channel_unit(self, session: Any) -> None:
        # Channel unit set via stream (sets the channel, not a lane); array
        # sample so the type matches the subsequent Waveform observe.
        session.ctx.stream("scope.temp", [24.0, 24.1], unit="degC")
        session.ctx.observe("scope.temp", Waveform(Y=[24.2, 24.3], dt=0.001))  # no unit
        assert session.ctx._observation_units["scope.temp"] == "degC"

    def test_observe_contradicting_unit_raises(self, session: Any) -> None:
        session.ctx.observe("scope.x", Waveform(Y=[1.0], dt=0.001), unit="V")
        with pytest.raises(ValueError, match="unit"):
            session.ctx.observe("scope.x", Waveform(Y=[2.0], dt=0.001), unit="A")
