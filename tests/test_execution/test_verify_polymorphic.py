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
                limit=Limit(low=0, high=10, units="V"),
            )


# --------------------------------------------------------------------- #
# Scalar path unchanged (regression guard)                               #
# --------------------------------------------------------------------- #


class TestScalarPathStillWorks:
    def test_scalar_with_limit_judges_pass(self, session: Any) -> None:
        m = _perform_verify("vout", 3.3, limit=Limit(low=3.0, high=3.6, units="V"))
        assert m.outcome == Outcome.PASSED
        assert m.value == 3.3

    def test_scalar_with_limit_raises_on_fail(self, session: Any) -> None:
        from litmus.execution.verify import LimitFailure

        with pytest.raises(LimitFailure):
            _perform_verify("vout", 12.0, limit=Limit(low=3.0, high=3.6, units="V"))

    def test_scalar_without_limit_raises_missing_limit(self, session: Any) -> None:
        from litmus.execution.verify import MissingLimitError

        with pytest.raises(MissingLimitError):
            _perform_verify("vout", 3.3)

    def test_none_value_with_limit_errors_outcome(self, session: Any) -> None:
        """value=None (couldn't measure) is a recordable scalar outcome:
        ERRORED, per design (``_compute_outcome`` returns ERRORED on None)."""
        m = _perform_verify("vout", None, limit=Limit(low=3.0, high=3.6, units="V"))
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
        m = _perform_verify("peak", peak, limit=Limit(low=0.0, high=5.0, units="V"))
        assert m.outcome == Outcome.PASSED
        # The artifact URI is queryable from the vector via out_*
        assert session.ctx._observations.get("scope.cap", "").startswith("channel://scope.cap")
