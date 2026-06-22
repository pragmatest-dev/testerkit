"""``test_phase`` data-stamp resolution under dirty git / mocks.

One name across CLI flag, profile facet, and data column. The resolver
demotes the **data stamp** to ``"development"`` when a run can't
produce trustworthy data (dirty git or active mocks); profile
selection reads the unmodified CLI value.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from litmus.execution.profiles import resolve_test_phase

pytest_plugins = ["pytester"]


class TestResolveTestPhase:
    def test_clean_git_no_mocks_honors_requested_phase(self) -> None:
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            assert resolve_test_phase("production") == "production"

    def test_dirty_git_demotes_regardless_of_request(self) -> None:
        with patch("litmus.execution._git.is_git_clean", return_value=False):
            assert resolve_test_phase("production") == "development"
            assert resolve_test_phase("validation") == "development"
            assert resolve_test_phase("characterization") == "development"

    def test_mocks_active_demotes_regardless_of_request(self) -> None:
        """Mocks active → stamp = development, even on a clean repo."""
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            assert resolve_test_phase("production", mocks_active=True) == "development"
            assert resolve_test_phase("validation", mocks_active=True) == "development"

    def test_dirty_git_plus_mocks_still_demotes(self) -> None:
        """Either dirty git OR mocks is sufficient for demotion."""
        with patch("litmus.execution._git.is_git_clean", return_value=False):
            assert resolve_test_phase("production", mocks_active=True) == "development"

    def test_none_requested_defaults_to_development(self) -> None:
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            assert resolve_test_phase(None) == "development"

    def test_mocks_short_circuits_before_git_check(self) -> None:
        """Mocks-active branch should not even consult git status."""
        with patch("litmus.execution._git.is_git_clean", side_effect=AssertionError):
            assert resolve_test_phase("production", mocks_active=True) == "development"


class TestMockInstrumentsNoUsageError:
    """The UsageError that blocked --mock-instruments in non-dev phases is gone.

    Mocks now compose with any phase: the profile selects on the raw
    CLI value (production markers/limits apply), the stamp is demoted
    to development, dashboards filter the row out.
    """

    def test_helper_returns_true_when_cli_flag_set(self) -> None:
        from types import SimpleNamespace
        from typing import cast

        from litmus.pytest_plugin.helpers import mocks_active as _mocks_active

        config = cast(
            "pytest.Config",
            SimpleNamespace(getoption=lambda _name, default=None: True),
        )
        assert _mocks_active(config) is True

    def test_helper_returns_false_when_no_mock_flag_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--no-mock-instruments`` overrides env var and YAML default."""
        from types import SimpleNamespace
        from typing import cast

        from litmus.pytest_plugin.helpers import mocks_active as _mocks_active

        monkeypatch.setenv("LITMUS_MOCK_INSTRUMENTS", "1")  # env says yes
        config = cast(
            "pytest.Config",
            SimpleNamespace(getoption=lambda _name, default=None: False),
        )
        assert _mocks_active(config) is False  # CLI explicit False wins

    def test_helper_returns_true_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace
        from typing import cast

        from litmus.pytest_plugin.helpers import mocks_active as _mocks_active

        monkeypatch.setenv("LITMUS_MOCK_INSTRUMENTS", "1")
        # CLI flag unset (None) → env wins
        config = cast(
            "pytest.Config",
            SimpleNamespace(getoption=lambda _name, default=None: default),
        )
        assert _mocks_active(config) is True

    def test_helper_returns_false_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace
        from typing import cast

        from litmus.pytest_plugin.helpers import mocks_active as _mocks_active

        monkeypatch.delenv("LITMUS_MOCK_INSTRUMENTS", raising=False)
        # CLI flag unset, env unset → falls through to project YAML (False by default).
        config = cast(
            "pytest.Config",
            SimpleNamespace(getoption=lambda _name, default=None: default),
        )
        assert _mocks_active(config) is False

    def test_no_longer_references_usage_error(self) -> None:
        """Sanity check: the fixture source doesn't still call UsageError."""
        import inspect

        from litmus.pytest_plugin import mock_instruments
        from litmus.pytest_plugin.helpers import mocks_active as _mocks_active

        fixture_src = inspect.getsource(mock_instruments)
        helper_src = inspect.getsource(_mocks_active)
        assert "UsageError" not in fixture_src
        assert "UsageError" not in helper_src
        assert "get_sequence_test_phase" not in fixture_src


class TestMethodMocksWarning:
    """``--test-phase=production`` + active method mocks → UserWarning at session start.

    Mocks are split-intent: legitimate for fault injection (OVP/OCP),
    suspicious for accidental-leftover. The warning surfaces them so
    operators can decide whether to scrub via a profile with
    ``mocks: []``.
    """

    def test_warns_on_method_mocks_in_production(self, pytester: pytest.Pytester) -> None:
        import textwrap
        from unittest.mock import patch

        pytester.makeini(
            textwrap.dedent(
                """
                [pytest]
                addopts = -p no:litmus -p litmus.pytest_plugin
                asyncio_default_fixture_loop_scope = function
                """
            )
        )
        pytester.makeconftest(
            textwrap.dedent(
                """
                import pytest

                class _Dmm:
                    def measure_dc_voltage(self):
                        return 999.0

                @pytest.fixture
                def dmm():
                    return _Dmm()
                """
            )
        )
        pytester.makepyfile(
            test_seq=textwrap.dedent(
                """
                class TestSeq:
                    def test_ovp(self, dmm):
                        assert dmm.measure_dc_voltage() == 4.5
                """
            )
        )
        (pytester.path / "test_seq.yaml").write_text(
            textwrap.dedent(
                """
                mocks:
                  - {target: "dmm.measure_dc_voltage", return_value: 4.5}
                """
            )
        )
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            result = pytester.runpytest("-v", "--test-phase=production", "--uut-serial=SN1")
        result.assert_outcomes(passed=1)
        result.stdout.fnmatch_lines(["*Method mocks active in test_phase='production'*"])

    def test_silent_in_development(self, pytester: pytest.Pytester) -> None:
        """Development phase suppresses the warning — mocks are expected there."""
        import textwrap

        pytester.makeini(
            textwrap.dedent(
                """
                [pytest]
                addopts = -p no:litmus -p litmus.pytest_plugin
                asyncio_default_fixture_loop_scope = function
                """
            )
        )
        pytester.makeconftest(
            textwrap.dedent(
                """
                import pytest

                class _Dmm:
                    def measure_dc_voltage(self):
                        return 999.0

                @pytest.fixture
                def dmm():
                    return _Dmm()
                """
            )
        )
        pytester.makepyfile(
            test_seq=textwrap.dedent(
                """
                class TestSeq:
                    def test_ovp(self, dmm):
                        assert dmm.measure_dc_voltage() == 4.5
                """
            )
        )
        (pytester.path / "test_seq.yaml").write_text(
            textwrap.dedent(
                """
                mocks:
                  - {target: "dmm.measure_dc_voltage", return_value: 4.5}
                """
            )
        )
        result = pytester.runpytest("-v", "--test-phase=development")
        result.assert_outcomes(passed=1)
        assert "Method mocks active" not in result.stdout.str()


class TestProfileFacetsStamping:
    """The run-record carries a ``profile_facets`` field (dict[str, str]).

    Captured from ``_collect_facet_flags_from_config`` — raw CLI values,
    not demoted. Combined with git SHA, this is the minimum
    reproducibility payload.
    """

    def test_testrun_default_profile_facets_is_empty_dict(self) -> None:
        from uuid import uuid4

        from litmus.data.models import UUT, TestRun

        run = TestRun(
            id=uuid4(),
            session_id=uuid4(),
            uut=UUT(serial="UUT001"),
            station_id="s",
        )
        assert run.profile_facets == {}

    def test_testrun_accepts_profile_facets_dict(self) -> None:
        from uuid import uuid4

        from litmus.data.models import UUT, TestRun

        run = TestRun(
            id=uuid4(),
            session_id=uuid4(),
            uut=UUT(serial="UUT001"),
            station_id="s",
            profile_facets={"test_phase": "production", "part": "tps54302"},
        )
        assert run.profile_facets == {
            "test_phase": "production",
            "part": "tps54302",
        }

    def test_parquet_metadata_uses_profile_facets_json_key(self) -> None:
        """Parquet file-level metadata key is ``profile_facets_json``."""
        from litmus.data.backends.parquet import _build_parquet_metadata

        meta = _build_parquet_metadata(
            profile_facets={"test_phase": "production", "part": "tps54302"}
        )
        assert b"profile_facets_json" in meta
        assert b"facets_json" not in meta

    def test_parquet_metadata_omits_key_when_no_facets(self) -> None:
        from litmus.data.backends.parquet import _build_parquet_metadata

        meta = _build_parquet_metadata(profile_facets=None)
        assert b"profile_facets_json" not in meta
        meta2 = _build_parquet_metadata(profile_facets={})
        assert b"profile_facets_json" not in meta2
