"""GH #79 — promote comparison-shaped ``assert`` statements into measurements.

Covers ``testerkit.pytest_plugin.assert_measurements``: every supported
pattern (chain incl. all four inclusivity combinations, single-bound
incl. reversed/both-variable operands, numeric ``==``/``!=``,
``pytest.approx``), the message -> name/unit split (trailing
``(unit)``), the "no message -> not promoted" carve-out, the "string
equality -> not promoted" carve-out, the opt-in gate (project config
default off, project config on, and the per-test marker override) as a
TRUE no-op when disabled (native failure diff intact, nothing
transformed, identical operand-evaluation behavior to vanilla pytest),
the assert-style FAIL message (comparison + a single ``where`` line),
and step/vector judgment status (PASSED/FAILED, not DONE) for a
promoted assert.

Two tiers, split for daemon-contention reasons (see the "Fast,
in-process" section docstring below):

* **Fast, in-process** — compiles+execs the AST-transformed source
  directly and stubs ``testerkit.execution.verify._perform_verify``
  (via ``monkeypatch``), so pattern recognition, comparator mapping,
  value/bound identification, and the assert-style failure message are
  all exercised against the REAL ``Limit``/``LimitFailure`` judging
  logic, with zero pytest subprocess and zero runs-daemon involvement.
  This is where the combinatorial pattern matrix lives.
* **Integration** (pytester-subprocess + canonical-data-dir polling,
  the pattern from ``test_verify_cascade.py``) — a small, deliberately
  minimal set proving the whole pipeline end to end: a promoted assert
  really does write a measurement row through ``run_scope.measure``,
  the opt-in gate/markers resolve correctly under real pytest
  collection, disabled really is untouched by pytest's own rewriter,
  and step/vector status really does land PASSED/FAILED. Kept small on
  purpose — ``test_chain_pattern_promotes_measurement[pass]`` was
  observed flaking under full-file load with "Daemon exited (code 1)
  before signaling ready" when this file carried ~40 subprocess-per-
  test cases; most of that coverage now lives in the fast tier instead
  of spawning a subprocess (and contending on the runs daemon) per
  parametrize case. Per ``tests/test_conventions.py`` / repo
  ``CLAUDE.md``: no ``_data_dir=tmp_path`` / ``--data-dir=<tmp_path>``
  anywhere; canonical data dir only, isolation is by unique
  ``--uut-serial``.
"""

from __future__ import annotations

import ast
import textwrap
import time
from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from testerkit.data.data_dir import resolve_data_dir
from testerkit.data.run_store import RunStore
from testerkit.execution.verify import LimitFailure
from testerkit.pytest_plugin import assert_measurements as am

pytest_plugins = ["pytester"]

_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:testerkit -p testerkit.pytest_plugin
    asyncio_default_fixture_loop_scope = function
    """
)

_ENABLED_YAML = textwrap.dedent(
    """
    name: testerkit
    assert_measurements: true
    """
)


# ===========================================================================
# Fast, in-process tier — no pytest subprocess, no runs daemon.
# ===========================================================================


def _promote(source: str, *, default_enabled: bool = True) -> dict:
    """AST-transform ``source`` and exec it; return the resulting namespace.

    Mirrors what ``install_assert_measurement_hook``'s wrapped
    ``rewrite_asserts`` does to a real test module, minus pytest's own
    involvement — so pattern recognition and the generated call shape
    are exercised exactly as they'd run under pytest, without needing
    pytest at all.
    """
    tree = ast.parse(textwrap.dedent(source))
    am.transform_module(tree, default_enabled=default_enabled)
    code = compile(tree, "<assert_measurements_test>", "exec")
    ns: dict = {}
    exec(code, ns)
    return ns


@pytest.fixture
def verify_spy(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    """Stub ``_perform_verify`` at the exact boundary GH #79 routes through.

    Judges using the REAL ``Limit.__contains__`` (the same comparator
    logic ``verify()`` uses) so comparator/bound correctness is
    genuinely exercised — it just skips ``run_scope.measure`` entirely
    (no active run scope, no parquet, no runs daemon needed). Each
    recorded call is appended as a ``SimpleNamespace(name, value,
    limit)`` to the returned list.
    """
    calls: list[SimpleNamespace] = []

    def _fake(name, value, limit=None, **kwargs):
        record = SimpleNamespace(name=name, value=value, limit=limit)
        calls.append(record)
        if limit is not None and value is not None and value not in limit:
            raise LimitFailure(name=name, value=value, limit=limit)
        return record

    monkeypatch.setattr("testerkit.execution.verify._perform_verify", _fake)
    return calls


def test_disabled_transform_leaves_assert_untouched() -> None:
    """DISABLED: the ``ast.Assert`` node is never replaced (module docstring, point 1)."""
    tree = ast.parse(
        textwrap.dedent(
            """
            def test_case(v):
                assert 20 <= v <= 80, "Case temperature (C)"
            """
        )
    )
    changed = am.transform_module(tree, default_enabled=False)
    assert changed is False
    rendered = ast.unparse(tree)
    assert "record_range" not in rendered
    assert "assert 20 <= v <= 80" in rendered


def test_no_message_assert_not_transformed() -> None:
    """No message -> not promoted, even when enabled (module docstring, point 4)."""
    tree = ast.parse(
        textwrap.dedent(
            """
            def test_case(v):
                assert 20 <= v <= 80
            """
        )
    )
    changed = am.transform_module(tree, default_enabled=True)
    assert changed is False
    assert "record_range" not in ast.unparse(tree)


@pytest.mark.parametrize(
    "shape,pass_value,fail_value,low,high,comparator",
    [
        ("20 <= v <= 80", 50.0, 91.0, 20.0, 80.0, "GELE"),
        ("0 <= v < 10", 0.0, 10.0, 0.0, 10.0, "GELT"),
        ("0 < v <= 10", 10.0, 0.0, 0.0, 10.0, "GTLE"),
        ("0 < v < 10", 5.0, 10.0, 0.0, 10.0, "GTLT"),
    ],
    ids=["gele", "gelt", "gtle", "gtlt"],
)
def test_chain_comparator_mapping(
    verify_spy: list[SimpleNamespace],
    shape: str,
    pass_value: float,
    fail_value: float,
    low: float,
    high: float,
    comparator: str,
) -> None:
    """All four chain inclusivity combinations map to the right ``Comparator``."""
    ns = _promote(
        f"""
        def test_case(v):
            assert {shape}, "Bound (V)"
        """
    )
    ns["test_case"](pass_value)
    assert verify_spy[-1].limit.low == low
    assert verify_spy[-1].limit.high == high
    assert verify_spy[-1].limit.comparator == comparator

    with pytest.raises(LimitFailure):
        ns["test_case"](fail_value)


@pytest.mark.parametrize(
    "shape,pass_value,fail_value,low,high,comparator",
    [
        ("v >= 3.0", 5.0, 1.0, 3.0, None, "GE"),
        ("v <= 3.6", 3.0, 4.0, None, 3.6, "LE"),
        ("v > 3.0", 3.1, 3.0, 3.0, None, "GT"),
        ("v < 3.6", 3.5, 3.6, None, 3.6, "LT"),
    ],
    ids=["ge", "le", "gt", "lt"],
)
def test_single_bound_comparator_mapping(
    verify_spy: list[SimpleNamespace],
    shape: str,
    pass_value: float,
    fail_value: float,
    low: float | None,
    high: float | None,
    comparator: str,
) -> None:
    """``x >= lo`` / ``x <= hi`` / ``x > lo`` / ``x < hi`` map to GE/LE/GT/LT."""
    ns = _promote(
        f"""
        def test_case(v):
            assert {shape}, "Rail (V)"
        """
    )
    ns["test_case"](pass_value)
    assert verify_spy[-1].value == pass_value
    assert verify_spy[-1].limit.low == low
    assert verify_spy[-1].limit.high == high
    assert verify_spy[-1].limit.comparator == comparator

    with pytest.raises(LimitFailure):
        ns["test_case"](fail_value)


@pytest.mark.parametrize(
    "shape,pass_value,fail_value,low,high,comparator",
    [
        ("20 <= v", 25.0, 15.0, 20.0, None, "GE"),  # lo <= x  =>  x >= lo
        ("10 >= v", 5.0, 15.0, None, 10.0, "LE"),  # hi >= x  =>  x <= hi
    ],
    ids=["reversed-ge", "reversed-le"],
)
def test_single_bound_reversed_operand(
    verify_spy: list[SimpleNamespace],
    shape: str,
    pass_value: float,
    fail_value: float,
    low: float | None,
    high: float | None,
    comparator: str,
) -> None:
    """Value on the RIGHT (``lo <= v``, ``hi >= v``) flips the operator sense correctly."""
    ns = _promote(
        f"""
        def test_case(v):
            assert {shape}, "Rail (V)"
        """
    )
    ns["test_case"](pass_value)
    assert verify_spy[-1].limit.low == low
    assert verify_spy[-1].limit.high == high
    assert verify_spy[-1].limit.comparator == comparator

    with pytest.raises(LimitFailure):
        ns["test_case"](fail_value)


def test_single_bound_both_variable_falls_back_to_left_as_value(
    verify_spy: list[SimpleNamespace],
) -> None:
    """Neither operand is a literal (``v >= threshold``) -> LEFT is the value.

    The bound is still captured dynamically (a variable, not a literal).
    """
    ns = _promote(
        """
        def test_case(v):
            threshold = 3.0
            assert v >= threshold, "Rail (V)"
        """
    )
    ns["test_case"](5.0)
    assert verify_spy[-1].value == 5.0
    assert verify_spy[-1].limit.low == 3.0
    assert verify_spy[-1].limit.comparator == "GE"


@pytest.mark.parametrize(
    "shape,pass_value,fail_value,nominal,comparator",
    [
        ("v == 5.0", 5.0, 6.0, 5.0, "EQ"),
        ("v != 5.0", 6.0, 5.0, 5.0, "NE"),
        ("5.0 == v", 5.0, 6.0, 5.0, "EQ"),  # reversed -- equality is symmetric
    ],
    ids=["eq", "ne", "eq-reversed"],
)
def test_equality_comparator_mapping(
    verify_spy: list[SimpleNamespace],
    shape: str,
    pass_value: float,
    fail_value: float,
    nominal: float,
    comparator: str,
) -> None:
    """Numeric ``x == n`` / ``x != n`` map to EQ/NE with ``nominal`` (no low/high band)."""
    ns = _promote(
        f"""
        def test_case(v):
            assert {shape}, "Match (V)"
        """
    )
    ns["test_case"](pass_value)
    assert verify_spy[-1].limit.nominal == nominal
    assert verify_spy[-1].limit.low is None
    assert verify_spy[-1].limit.high is None
    assert verify_spy[-1].limit.comparator == comparator

    with pytest.raises(LimitFailure):
        ns["test_case"](fail_value)


def test_string_equality_not_promoted(verify_spy: list[SimpleNamespace]) -> None:
    """``s == "literal"`` matches the Eq shape but is NOT promoted at runtime (non-numeric).

    Behaves exactly like the plain ``assert`` it replaced: no call to
    ``_perform_verify``, and a normal ``AssertionError`` on mismatch.
    """
    ns = _promote(
        """
        def test_case(s):
            assert s == "abc", "Label"
        """
    )
    ns["test_case"]("abc")
    assert verify_spy == []
    with pytest.raises(AssertionError) as excinfo:
        ns["test_case"]("xyz")
    assert not isinstance(excinfo.value, LimitFailure)
    assert verify_spy == []


def test_approx_pattern(verify_spy: list[SimpleNamespace]) -> None:
    """``x == pytest.approx(n, rel=...)`` — tolerance becomes a GELE low/high band."""
    ns = _promote(
        """
        import pytest

        def test_case(v):
            assert v == pytest.approx(5.0, rel=0.01), "Match (V)"
        """
    )
    ns["test_case"](5.001)
    assert verify_spy[-1].limit.nominal == 5.0
    assert verify_spy[-1].limit.low == pytest.approx(4.95)
    assert verify_spy[-1].limit.high == pytest.approx(5.05)
    assert verify_spy[-1].limit.comparator == "GELE"

    with pytest.raises(LimitFailure):
        ns["test_case"](5.5)


# ---------------------------------------------------------------------------
# Part A: the FAIL message reads like the original assert, not just
# verify's value-vs-limit format. One representative case per recorder.
# ---------------------------------------------------------------------------


def test_chain_fail_message_is_assert_style(verify_spy: list[SimpleNamespace]) -> None:
    ns = _promote(
        """
        def test_case(temp):
            assert 20 <= temp <= 80, "Case temperature (C)"
        """
    )
    with pytest.raises(LimitFailure) as excinfo:
        ns["test_case"](91.0)
    message = str(excinfo.value)
    assert message.splitlines() == [
        "Case temperature (C)",
        "assert 20 <= 91.0 <= 80",
        " +  where 91.0 = temp",
    ]
    assert isinstance(excinfo.value.__cause__, LimitFailure)
    assert excinfo.value.__cause__ is not excinfo.value


def test_single_bound_fail_message_is_assert_style(verify_spy: list[SimpleNamespace]) -> None:
    ns = _promote(
        """
        def test_case(v):
            assert v >= 3.0, "Rail (V)"
        """
    )
    with pytest.raises(LimitFailure) as excinfo:
        ns["test_case"](1.0)
    assert str(excinfo.value) == "Rail (V)\nassert 1.0 >= 3.0\n +  where 1.0 = v"


def test_equality_fail_message_is_assert_style(verify_spy: list[SimpleNamespace]) -> None:
    ns = _promote(
        """
        def test_case(v):
            assert v == 5.0, "Match (V)"
        """
    )
    with pytest.raises(LimitFailure) as excinfo:
        ns["test_case"](6.0)
    assert str(excinfo.value) == "Match (V)\nassert 6.0 == 5.0\n +  where 6.0 = v"


def test_approx_fail_message_is_assert_style(verify_spy: list[SimpleNamespace]) -> None:
    ns = _promote(
        """
        import pytest

        def test_case(v):
            assert v == pytest.approx(5.0, rel=0.01), "Match (V)"
        """
    )
    with pytest.raises(LimitFailure) as excinfo:
        ns["test_case"](5.5)
    message = str(excinfo.value)
    assert message.startswith("Match (V)\nassert 5.5 == pytest.approx(5.0, rel=0.01)\n")
    assert " +  where 5.5 = v" in message


def test_non_numeric_fallback_message_is_assert_style() -> None:
    """The non-numeric fallback (point 6) ALSO gets the assert-style treatment."""
    ns = _promote(
        """
        def test_case(s):
            assert s == "abc", "Label"
        """
    )
    with pytest.raises(AssertionError) as excinfo:
        ns["test_case"]("xyz")
    assert str(excinfo.value) == "Label\nassert 'xyz' == 'abc'\n +  where 'xyz' = s"


def test_plain_verify_limit_failure_message_unchanged() -> None:
    """A hand-written ``verify(...)`` failure keeps its ORDINARY wording.

    GH #79's assert-style message lives entirely inside the
    promoted-assert recorders (``_reraise_assert_style`` builds a
    SECOND ``LimitFailure`` and overrides only its rendered message) —
    ``LimitFailure`` / ``_perform_verify`` themselves are never touched
    by this feature, so this is really a regression lock on that
    separation, not a promoted-assert test.
    """
    from testerkit.models.test_config import Limit

    limit = Limit(low=3.0, high=3.6, unit="V")
    exc = LimitFailure(name="v_rail", value=5.0, limit=limit)
    message = str(exc)
    assert message == f"v_rail = 5.0 V fails {limit!r}"
    assert "assert" not in message
    assert "where" not in message


# ===========================================================================
# Integration tier — pytester subprocess + canonical-data-dir polling.
# Kept intentionally small (daemon-contention hardening, see module docstring).
# ===========================================================================


# Project-local results via the repo's own testerkit.yaml (see
# tests/conftest.py, which pins TESTERKIT_HOME so pytester subprocesses
# resolve to this same canonical directory).
_CANONICAL_RESULTS = resolve_data_dir()


@pytest.fixture(scope="module", autouse=True)
def _runs_daemon_for_assert_measurements() -> Iterator[None]:
    """Keep the runs daemon alive for this module's pytester-subprocess tests.

    See ``test_verify_cascade.py`` for the full rationale — without an
    acquire-on-this-process, the daemon that materializes parquet from
    the subprocess's events never spawns.
    """
    store = RunStore()
    try:
        yield
    finally:
        store.close()


def _wait_for_parquet(serial: str, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = list(_CANONICAL_RESULTS.glob(f"runs/**/*_{serial}.parquet"))
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
        time.sleep(0.2)
    raise AssertionError(f"no measurement parquet for serial={serial!r}")


def _carrier_rows(serial: str) -> list[dict]:
    """All step/vector rows for ``serial`` (the measurement carriers)."""
    parquet = _wait_for_parquet(serial)
    table = pq.read_table(parquet)
    return [r for r in table.to_pylist() if r.get("record_type") in ("step", "vector")]


def _measurements(serial: str, name: str) -> list[dict]:
    """All nested ``measurements`` entries named ``name`` across carrier rows."""
    return [
        m for r in _carrier_rows(serial) for m in (r.get("measurements") or []) if m["name"] == name
    ]


def _all_measurements(serial: str) -> list[dict]:
    return [m for r in _carrier_rows(serial) for m in (r.get("measurements") or [])]


def _carrier_row_with_measurement(serial: str, name: str) -> dict:
    """The full step/vector row (not just the nested measurement) carrying ``name``."""
    for r in _carrier_rows(serial):
        for m in r.get("measurements") or []:
            if m["name"] == name:
                return r
    raise AssertionError(f"no carrier row with measurement {name!r}")


def _run(
    pytester: pytest.Pytester,
    body: str,
    *,
    project_yaml: str | None = None,
    expect_failed: bool = False,
) -> str:
    pytester.makeini(_INI)
    pytester.makepyfile(test_seq=body)
    if project_yaml is not None:
        (pytester.path / "testerkit.yaml").write_text(project_yaml)
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--uut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    if expect_failed:
        result.assert_outcomes(failed=1)
    else:
        result.assert_outcomes(passed=1)
    return serial


@pytest.mark.parametrize("temp,outcome", [(25, "passed"), (200, "failed")], ids=["pass", "fail"])
def test_chain_pattern_promotes_measurement(
    pytester: pytest.Pytester, temp: int, outcome: str
) -> None:
    """``lo <= x <= hi`` (GELE) promotes on BOTH pass and fail (the issue's own example).

    End-to-end proof that the real pipeline (real ``run_scope.measure``,
    real parquet row) matches what the fast/mocked tier already checked
    in isolation.
    """
    serial = _run(
        pytester,
        f"""
        def test_case():
            temp = {temp}
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """,
        project_yaml=_ENABLED_YAML,
        expect_failed=(outcome == "failed"),
    )
    rows = _measurements(serial, "Case temperature")
    assert len(rows) == 1
    row = rows[0]
    assert row["value"] == temp
    assert row["unit"] == "°C"
    assert row["limit_low"] == 20
    assert row["limit_high"] == 80
    assert row["limit_comparator"] == "GELE"
    assert row["outcome"] == outcome


def test_message_without_unit_has_empty_unit(pytester: pytest.Pytester) -> None:
    """No trailing ``(unit)`` -> unit is empty, name is the whole message."""
    serial = _run(
        pytester,
        """
        def test_case():
            temp = 25
            assert 20 <= temp <= 80, "Case temperature"
        """,
        project_yaml=_ENABLED_YAML,
    )
    rows = _measurements(serial, "Case temperature")
    assert len(rows) == 1
    assert rows[0]["unit"] == ""


def test_bare_assert_without_message_not_promoted(pytester: pytest.Pytester) -> None:
    """A comparison-shaped assert with NO message stays a plain assert.

    Even with the gate on, it must record zero measurements — the
    static ``node.msg is None`` check skips promotion entirely. The
    step still resolves PASSED (not DONE) via pytest's OWN
    ``pytest_assertion_pass`` hook (unrelated to this feature, and
    unaffected by it).
    """
    serial = _run(
        pytester,
        """
        def test_case():
            temp = 25
            assert 20 <= temp <= 80
        """,
        project_yaml=_ENABLED_YAML,
    )
    assert _all_measurements(serial) == []
    row = _carrier_rows(serial)[0]
    assert row["step_outcome"] == "passed"


def test_disabled_by_default_not_promoted(pytester: pytest.Pytester) -> None:
    """No ``assert_measurements:`` in testerkit.yaml, no marker -> not promoted.

    The AST transform never fires for this function at all (the
    ``ast.Assert`` node is untouched) — no measurement is recorded,
    whether the comparison passes or fails.
    """
    serial = _run(
        pytester,
        """
        def test_case():
            temp = 25
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """,
        project_yaml=None,
    )
    assert _all_measurements(serial) == []

    fail_serial = _run(
        pytester,
        """
        def test_case():
            temp = 200
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """,
        project_yaml=None,
        expect_failed=True,
    )
    assert _all_measurements(fail_serial) == []


def test_disabled_native_failure_diff_intact(pytester: pytest.Pytester) -> None:
    """DISABLED: pytest's own rewritten failure diff is untouched.

    Proves the assert is a real, un-rewritten ``ast.Assert`` — a
    promoted assert would show a hand-formatted message instead of
    pytest's own value-substituted explanation, and would mention our
    recorder in the traceback.
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq="""
        def test_case():
            v = 1.0
            assert v >= 3.0, "Rail (V)"
        """
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--uut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(failed=1)
    # Pytest's native rewritten explanation substitutes the runtime value
    # into the ORIGINAL source expression.
    result.stdout.fnmatch_lines(["*assert 1.0 >= 3.0*"])
    output = str(result.stdout)
    assert "record_range" not in output
    assert "__testerkit_assert_measurements__" not in output
    assert _all_measurements(serial) == []


def test_disabled_chain_matches_vanilla_pytest_operand_evaluation(
    pytester: pytest.Pytester,
) -> None:
    """DISABLED: a chain assert evaluates operands EXACTLY like vanilla pytest.

    Verified independently (see module docstring, point 7 of
    ``assert_measurements.py``):
    ``_pytest.assertion.rewrite.AssertionRewriter.visit_Compare`` already
    evaluates every comparator in a chain up front to build its diff,
    for ANY chained-comparison assert pytest rewrites — a plain
    ``assert temp <= 5 <= _hi()`` under bare pytest (no TesterKit plugin
    at all) calls ``_hi()`` even though the first comparison already
    fails; raw un-rewritten Python bytecode would not. So the correct
    "unchanged" assertion here is that ``_hi()`` STILL runs when
    disabled — reproducing pytest's own eager evaluation, not
    introducing new eagerness of our own. (The definitive proof that
    OUR transform didn't touch this assert is
    ``test_disabled_native_failure_diff_intact``, which checks the
    traceback never mentions our recorder.)
    """
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq="""
        from pathlib import Path

        def _hi():
            Path("hi_called.marker").write_text("called")
            return 80

        def test_case():
            temp = 200
            assert temp <= 5 <= _hi(), "msg (V)"
        """
    )
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--uut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(failed=1)
    assert (pytester.path / "hi_called.marker").exists()
    assert _all_measurements(serial) == []


def test_marker_enables_regardless_of_project_default(pytester: pytest.Pytester) -> None:
    """The marker promotes even with the project-wide gate off."""
    serial = _run(
        pytester,
        """
        import pytest

        @pytest.mark.testerkit_assert_measurements
        def test_case():
            temp = 25
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """,
        project_yaml=None,
    )
    rows = _measurements(serial, "Case temperature")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "passed"


def test_module_pytestmark_enables_all_tests_in_file(pytester: pytest.Pytester) -> None:
    """A module-level ``pytestmark`` also force-enables, without a project config flag."""
    serial = _run(
        pytester,
        """
        import pytest

        pytestmark = pytest.mark.testerkit_assert_measurements

        def test_case():
            temp = 25
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """,
        project_yaml=None,
    )
    rows = _measurements(serial, "Case temperature")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "passed"


def test_promoted_pass_drives_step_outcome_passed_not_done(pytester: pytest.Pytester) -> None:
    """A promoted PASS feeds ``mark_step_judgment_intent`` (via ``_perform_verify``
    -> ``run_scope.measure``), so the step resolves PASSED — not the DONE
    recorder-default a limitless measurement would get."""
    serial = _run(
        pytester,
        """
        def test_case():
            temp = 25
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """,
        project_yaml=_ENABLED_YAML,
    )
    row = _carrier_row_with_measurement(serial, "Case temperature")
    assert row["step_outcome"] == "passed"
    assert row["run_outcome"] == "passed"


def test_promoted_fail_raises_limit_failure_and_drives_step_failed(
    pytester: pytest.Pytester,
) -> None:
    """A promoted FAIL raises ``LimitFailure`` with an assert-style message,
    drives the step to FAILED, and still records the FAILED measurement
    (Part A: message wording changes, the recorded row does not)."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq="""
        def test_case():
            temp = 200
            assert 20 <= temp <= 80, "Case temperature (°C)"
        """
    )
    (pytester.path / "testerkit.yaml").write_text(_ENABLED_YAML)
    serial = f"test-{uuid4().hex[:8]}"
    result = pytester.runpytest_subprocess(
        f"--uut-serial={serial}",
        "--mock-instruments",
        "-q",
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*LimitFailure*Case temperature*",
            "*assert 20 <= 200 <= 80*",
            "*where 200 = temp*",
        ]
    )
    row = _carrier_row_with_measurement(serial, "Case temperature")
    measurement = _measurements(serial, "Case temperature")[0]
    assert measurement["outcome"] == "failed"
    assert row["step_outcome"] == "failed"
    assert row["run_outcome"] == "failed"
