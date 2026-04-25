"""Coverage for ``required_inputs`` resolution and ``default_profile``.

The resolver runs at session start and walks every declared input
through CLI flag → env var → ``litmus.prompts.ask`` (in that order),
fail-fast on missing values. ``default_profile`` + ``--no-profile``
control disambiguation when profiles are declared.
"""

from __future__ import annotations

import pytest

from litmus.execution.profiles import (
    required_input_key_to_cli_flag,
    required_input_key_to_env_var,
    resolve_default_profile,
    resolve_required_inputs,
)
from litmus.models.config import PromptConfig
from litmus.models.project import ProfileConfig, ProjectConfig
from litmus.prompts import set_prompt_handler


class _StubConfig:
    """Minimal pytest config stand-in exposing ``getoption``."""

    def __init__(self, options: dict[str, str | None] | None = None) -> None:
        self._opts = options or {}

    def getoption(self, name: str, default: object = None) -> object:
        return self._opts.get(name, default)


# ---------------------------------------------------------------------------
# key → flag / env helpers
# ---------------------------------------------------------------------------


def test_cli_flag_underscore_to_hyphen() -> None:
    assert required_input_key_to_cli_flag("lot_number") == "--lot-number"
    assert required_input_key_to_cli_flag("wafer_id") == "--wafer-id"


def test_env_var_uppercase_with_prefix() -> None:
    assert required_input_key_to_env_var("lot_number") == "LITMUS_LOT_NUMBER"
    assert required_input_key_to_env_var("operator") == "LITMUS_OPERATOR"


# ---------------------------------------------------------------------------
# resolve_required_inputs — CLI > env > prompt fallthrough
# ---------------------------------------------------------------------------


def test_resolve_returns_empty_when_nothing_declared() -> None:
    project = ProjectConfig(name="p")
    config = _StubConfig({})
    assert resolve_required_inputs(project, config) == {}


def test_cli_flag_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITMUS_LOT_NUMBER", "from_env")
    project = ProjectConfig(
        name="p",
        required_inputs={"lot_number": PromptConfig(message="lot?", prompt_type="input")},
    )
    config = _StubConfig({"--lot-number": "from_flag"})
    assert resolve_required_inputs(project, config) == {"lot_number": "from_flag"}


def test_env_var_wins_when_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITMUS_LOT_NUMBER", "L42")
    project = ProjectConfig(
        name="p",
        required_inputs={"lot_number": PromptConfig(message="lot?", prompt_type="input")},
    )
    config = _StubConfig({})
    assert resolve_required_inputs(project, config) == {"lot_number": "L42"}


def test_prompt_handler_fallthrough_when_no_flag_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LITMUS_WAFER_ID", raising=False)
    set_prompt_handler(lambda cfg: "W17")
    try:
        project = ProjectConfig(
            name="p",
            required_inputs={"wafer_id": PromptConfig(message="wafer?", prompt_type="input")},
        )
        config = _StubConfig({})
        assert resolve_required_inputs(project, config) == {"wafer_id": "W17"}
    finally:
        set_prompt_handler(None)


def test_missing_required_input_raises_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LITMUS_LOT_NUMBER", raising=False)
    set_prompt_handler(None)  # No handler; auto-confirm not set; non-tty -> error
    project = ProjectConfig(
        name="p",
        required_inputs={"lot_number": PromptConfig(message="lot?", prompt_type="input")},
    )
    config = _StubConfig({})
    with pytest.raises(pytest.UsageError, match="lot_number"):
        resolve_required_inputs(project, config)


def test_empty_prompt_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITMUS_LOT_NUMBER", raising=False)
    set_prompt_handler(lambda cfg: "")
    try:
        project = ProjectConfig(
            name="p",
            required_inputs={"lot_number": PromptConfig(message="lot?", prompt_type="input")},
        )
        config = _StubConfig({})
        with pytest.raises(pytest.UsageError, match="lot_number"):
            resolve_required_inputs(project, config)
    finally:
        set_prompt_handler(None)


# ---------------------------------------------------------------------------
# resolve_default_profile — disambiguation rules
# ---------------------------------------------------------------------------


def test_no_profiles_declared_returns_none() -> None:
    project = ProjectConfig(name="p")
    assert resolve_default_profile(None, {}, False, project) is None


def test_explicit_profile_name_passes_through() -> None:
    project = ProjectConfig(
        name="p",
        profiles={"prod": ProfileConfig(facets={"test_phase": "production"})},
    )
    assert resolve_default_profile("prod", {}, False, project) == "prod"


def test_facet_flags_bypass_default() -> None:
    project = ProjectConfig(
        name="p",
        default_profile="dev",
        profiles={
            "dev": ProfileConfig(facets={"test_phase": "development"}),
            "prod": ProfileConfig(facets={"test_phase": "production"}),
        },
    )
    # User passed --test-phase=production; default_profile should not apply.
    assert resolve_default_profile(None, {"test_phase": "production"}, False, project) is None


def test_no_profile_flag_bypasses_default() -> None:
    project = ProjectConfig(
        name="p",
        default_profile="prod",
        profiles={"prod": ProfileConfig(facets={"test_phase": "production"})},
    )
    assert resolve_default_profile(None, {}, True, project) is None


def test_default_profile_applied_when_bare_pytest() -> None:
    project = ProjectConfig(
        name="p",
        default_profile="dev",
        profiles={
            "dev": ProfileConfig(facets={"test_phase": "development"}),
            "prod": ProfileConfig(facets={"test_phase": "production"}),
        },
    )
    assert resolve_default_profile(None, {}, False, project) == "dev"


def test_profiles_declared_no_selection_no_default_raises() -> None:
    project = ProjectConfig(
        name="p",
        profiles={
            "dev": ProfileConfig(facets={"test_phase": "development"}),
            "prod": ProfileConfig(facets={"test_phase": "production"}),
        },
    )
    with pytest.raises(pytest.UsageError, match="default_profile"):
        resolve_default_profile(None, {}, False, project)
