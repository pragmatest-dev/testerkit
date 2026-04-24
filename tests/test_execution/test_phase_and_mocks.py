"""``test_phase`` data-stamp resolution under dirty git / mocks.

One name across CLI flag, profile facet, and data column. The resolver
demotes the **data stamp** to ``"development"`` when a run can't
produce trustworthy data (dirty git or active mocks); profile
selection reads the unmodified CLI value.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from litmus.execution.plugin import _resolve_test_phase


class TestResolveTestPhase:
    def test_clean_git_no_mocks_honors_requested_phase(self) -> None:
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            assert _resolve_test_phase("production") == "production"

    def test_dirty_git_demotes_regardless_of_request(self) -> None:
        with patch("litmus.execution._git.is_git_clean", return_value=False):
            assert _resolve_test_phase("production") == "development"
            assert _resolve_test_phase("validation") == "development"
            assert _resolve_test_phase("characterization") == "development"

    def test_mocks_active_demotes_regardless_of_request(self) -> None:
        """Mocks active → stamp = development, even on a clean repo."""
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            assert _resolve_test_phase("production", mocks_active=True) == "development"
            assert _resolve_test_phase("validation", mocks_active=True) == "development"

    def test_dirty_git_plus_mocks_still_demotes(self) -> None:
        """Either dirty git OR mocks is sufficient for demotion."""
        with patch("litmus.execution._git.is_git_clean", return_value=False):
            assert _resolve_test_phase("production", mocks_active=True) == "development"

    def test_none_requested_defaults_to_development(self) -> None:
        with patch("litmus.execution._git.is_git_clean", return_value=True):
            assert _resolve_test_phase(None) == "development"

    def test_mocks_short_circuits_before_git_check(self) -> None:
        """Mocks-active branch should not even consult git status."""
        with patch("litmus.execution._git.is_git_clean", side_effect=AssertionError):
            assert _resolve_test_phase("production", mocks_active=True) == "development"


class TestMockInstrumentsNoUsageError:
    """The UsageError that blocked --mock-instruments in non-dev phases is gone.

    Mocks now compose with any phase: the profile selects on the raw
    CLI value (production markers/limits apply), the stamp is demoted
    to development, dashboards filter the row out.
    """

    def test_helper_returns_true_when_cli_flag_set(self) -> None:
        from types import SimpleNamespace
        from typing import cast

        from litmus.execution.plugin import _mocks_active

        config = cast("pytest.Config", SimpleNamespace(getoption=lambda _: True))
        assert _mocks_active(config) is True

    def test_helper_returns_true_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace
        from typing import cast

        from litmus.execution.plugin import _mocks_active

        monkeypatch.setenv("LITMUS_MOCK_INSTRUMENTS", "1")
        config = cast("pytest.Config", SimpleNamespace(getoption=lambda _: False))
        assert _mocks_active(config) is True

    def test_helper_returns_false_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace
        from typing import cast

        from litmus.execution.plugin import _mocks_active

        monkeypatch.delenv("LITMUS_MOCK_INSTRUMENTS", raising=False)
        config = cast("pytest.Config", SimpleNamespace(getoption=lambda _: False))
        assert _mocks_active(config) is False

    def test_no_longer_references_usage_error(self) -> None:
        """Sanity check: the fixture source doesn't still call UsageError."""
        import inspect

        from litmus.execution.plugin import _mocks_active, mock_instruments

        fixture_src = inspect.getsource(mock_instruments)
        helper_src = inspect.getsource(_mocks_active)
        assert "UsageError" not in fixture_src
        assert "UsageError" not in helper_src
        assert "get_sequence_test_phase" not in fixture_src


class TestProfileFacetsStamping:
    """The run-record carries a ``profile_facets`` field (dict[str, str]).

    Captured from ``_collect_facet_flags_from_config`` — raw CLI values,
    not demoted. Combined with git SHA, this is the minimum
    reproducibility payload.
    """

    def test_testrun_default_profile_facets_is_empty_dict(self) -> None:
        from uuid import uuid4

        from litmus.data.models import DUT, TestRun

        run = TestRun(
            id=uuid4(),
            session_id=uuid4(),
            dut=DUT(serial="DUT001"),
            station_id="s",
            test_sequence_id="seq",
        )
        assert run.profile_facets == {}

    def test_testrun_accepts_profile_facets_dict(self) -> None:
        from uuid import uuid4

        from litmus.data.models import DUT, TestRun

        run = TestRun(
            id=uuid4(),
            session_id=uuid4(),
            dut=DUT(serial="DUT001"),
            station_id="s",
            test_sequence_id="seq",
            profile_facets={"test_phase": "production", "product": "tps54302"},
        )
        assert run.profile_facets == {
            "test_phase": "production",
            "product": "tps54302",
        }

    def test_parquet_metadata_uses_profile_facets_json_key(self) -> None:
        """Parquet file-level metadata key is ``profile_facets_json``."""
        from litmus.data.backends.parquet import _build_parquet_metadata

        meta = _build_parquet_metadata(
            profile_facets={"test_phase": "production", "product": "tps54302"}
        )
        assert b"profile_facets_json" in meta
        assert b"facets_json" not in meta

    def test_parquet_metadata_omits_key_when_no_facets(self) -> None:
        from litmus.data.backends.parquet import _build_parquet_metadata

        meta = _build_parquet_metadata(profile_facets=None)
        assert b"profile_facets_json" not in meta
        meta2 = _build_parquet_metadata(profile_facets={})
        assert b"profile_facets_json" not in meta2
