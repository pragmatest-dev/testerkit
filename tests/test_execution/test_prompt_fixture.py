"""Coverage for the ``prompt`` fixture and the ``litmus.prompts`` core.

The fixture is purely marker-driven: ``litmus_prompts`` markers in scope
populate a name-keyed dict; ``prompt(name)`` resolves an entry, and
``prompt()`` works as a shortcut when exactly one entry is in scope.
Routing of the prompt itself goes through :mod:`litmus.prompts` —
explicit handler → ``LITMUS_PROMPT_MODE=auto-confirm`` → tty fallback →
``PromptUnavailableError``.
"""

from __future__ import annotations

import textwrap

import pytest

from litmus.models.config import PromptConfig
from litmus.prompts import (
    PromptUnavailableError,
    ask,
    get_prompt_handler,
    set_prompt_handler,
)

pytest_plugins = ["pytester"]


_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.execution.plugin
    asyncio_default_fixture_loop_scope = function
    """
)


# ---------------------------------------------------------------------------
# litmus.prompts.ask — routing precedence
# ---------------------------------------------------------------------------


def test_ask_uses_explicit_handler() -> None:
    captured: list[PromptConfig] = []

    def handler(config: PromptConfig) -> str:
        captured.append(config)
        return "from-handler"

    assert get_prompt_handler() is None
    set_prompt_handler(handler)
    try:
        result = ask(PromptConfig(message="go", prompt_type="confirm"))
    finally:
        set_prompt_handler(None)

    assert result == "from-handler"
    assert captured[0].message == "go"


def test_ask_auto_confirm_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITMUS_PROMPT_MODE", "auto-confirm")
    set_prompt_handler(None)

    assert ask(PromptConfig(message="m", prompt_type="confirm")) is True
    assert ask(PromptConfig(message="m", prompt_type="choice", choices=["a", "b"])) == "a"
    assert ask(PromptConfig(message="m", prompt_type="input")) == ""


def test_ask_raises_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITMUS_PROMPT_MODE", raising=False)
    set_prompt_handler(None)
    # Subprocess pytester ensures stdin is not a tty in this run; in this
    # in-process test we force the same condition by stubbing isatty.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(PromptUnavailableError):
        ask(PromptConfig(message="m", prompt_type="confirm"))


# ---------------------------------------------------------------------------
# prompt fixture — marker resolution
# ---------------------------------------------------------------------------


def test_prompt_fixture_single_entry_implicit_key(pytester: pytest.Pytester) -> None:
    """One marker entry in scope → ``prompt()`` resolves it without a key."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import os
            import pytest

            @pytest.fixture(autouse=True)
            def _auto_confirm(monkeypatch):
                monkeypatch.setenv("LITMUS_PROMPT_MODE", "auto-confirm")

            @pytest.mark.litmus_prompts(only={"message": "go", "prompt_type": "confirm"})
            def test_one(prompt):
                assert prompt() is True
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_prompt_fixture_named_lookup(pytester: pytest.Pytester) -> None:
    """Multi-entry marker: explicit key resolves the right entry per kind."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            from litmus.prompts import set_prompt_handler

            @pytest.fixture
            def captured():
                seen = []
                def handler(config):
                    seen.append(config)
                    if config.prompt_type == "choice":
                        return config.choices[0]
                    if config.prompt_type == "input":
                        return "DUT-0042"
                    return True
                set_prompt_handler(handler)
                try:
                    yield seen
                finally:
                    set_prompt_handler(None)

            @pytest.mark.litmus_prompts(
                op_setup={"message": "Insert DUT", "prompt_type": "confirm"},
                pick={"message": "Pick fixture", "prompt_type": "choice",
                      "choices": ["bench_01", "bench_02"]},
                serial={"message": "Enter serial", "prompt_type": "input"},
            )
            def test_kinds(prompt, captured):
                assert prompt("op_setup") is True
                assert prompt("pick") == "bench_01"
                assert prompt("serial") == "DUT-0042"
                assert [c.message for c in captured] == [
                    "Insert DUT", "Pick fixture", "Enter serial",
                ]
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_prompt_fixture_unknown_key_errors(pytester: pytest.Pytester) -> None:
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.litmus_prompts(only={"message": "m", "prompt_type": "confirm"})
            def test_typo(prompt):
                prompt("oonly")
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*no such key in litmus_prompts markers*"])


def test_prompt_fixture_implicit_with_zero_entries_errors(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_no_marker(prompt):
                prompt()
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*no litmus_prompts markers are in scope*"])


def test_prompt_fixture_implicit_with_multiple_entries_errors(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.mark.litmus_prompts(
                a={"message": "a", "prompt_type": "confirm"},
                b={"message": "b", "prompt_type": "confirm"},
            )
            def test_ambiguous(prompt):
                prompt()
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Pass an explicit key*"])


def test_prompt_fixture_sidecar_yaml(pytester: pytest.Pytester) -> None:
    """``litmus_prompts`` declared via sidecar YAML resolves identically."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest

            @pytest.fixture(autouse=True)
            def _auto_confirm(monkeypatch):
                monkeypatch.setenv("LITMUS_PROMPT_MODE", "auto-confirm")

            def test_sidecar(prompt):
                assert prompt("setup") is True
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            tests:
              test_sidecar:
                config:
                  - litmus_prompts:
                      setup: {message: "Insert DUT", prompt_type: confirm}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_prompt_fixture_per_test_overrides_file_level(
    pytester: pytest.Pytester,
) -> None:
    """Per-test ``litmus_prompts`` entry with same key wins over file-level."""
    pytester.makeini(_INI)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            import pytest
            from litmus.prompts import set_prompt_handler

            @pytest.fixture
            def captured():
                seen = []
                def handler(config):
                    seen.append(config.message)
                    return True
                set_prompt_handler(handler)
                try:
                    yield seen
                finally:
                    set_prompt_handler(None)

            def test_one(prompt, captured):
                prompt("setup")
                assert captured == ["per-test message"]
            """
        )
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            config:
              - litmus_prompts:
                  setup: {message: "file-level message", prompt_type: confirm}
            tests:
              test_one:
                config:
                  - litmus_prompts:
                      setup: {message: "per-test message", prompt_type: confirm}
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)
