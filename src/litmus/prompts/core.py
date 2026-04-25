"""Runner-agnostic operator prompt core.

The single entry point is :func:`ask`. Resolution order for *how* a prompt
is presented:

1. An explicit handler installed via :func:`set_prompt_handler` (e.g. by a
   UI runner that knows how to dispatch to a browser/operator surface).
2. ``LITMUS_PROMPT_MODE=auto-confirm`` — non-interactive auto-resolution
   for CI / smoke runs.
3. The default TTY handler — prints to ``stdout`` and reads ``stdin``.
4. ``PromptUnavailableError`` — no UI, no tty, no auto-confirm.

Everything in this module is runner-agnostic. Pytest, OpenHTF, and plain
scripts all bind into the same handler ContextVar.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from litmus.models.config import PromptConfig

PromptHandler = Callable[[PromptConfig], Any]


class PromptUnavailableError(RuntimeError):
    """No way to ask the operator: no UI handler, no tty, no auto-confirm."""


_prompt_handler_var: ContextVar[PromptHandler | None] = ContextVar(
    "_prompt_handler_var", default=None
)


def set_prompt_handler(handler: PromptHandler | None) -> None:
    """Install (or clear) the active prompt handler.

    UI runners call this to route prompts through their own surface; tests
    use ``set_prompt_handler(None)`` in finally blocks to restore the
    default routing.
    """
    _prompt_handler_var.set(handler)


def get_prompt_handler() -> PromptHandler | None:
    """Return the currently installed prompt handler, if any."""
    return _prompt_handler_var.get()


def ask(config: PromptConfig) -> Any:
    """Present a prompt to the operator and return their response.

    Returns:
        - ``confirm``: ``True`` once acknowledged.
        - ``choice``: the selected string from ``config.choices``.
        - ``input``: the operator's typed string.
    """
    handler = _prompt_handler_var.get()
    if handler is not None:
        return handler(config)

    if os.environ.get("LITMUS_PROMPT_MODE") == "auto-confirm":
        return _auto_confirm(config)

    if sys.stdin.isatty():
        return _tty_handler(config)

    raise PromptUnavailableError(
        f"Cannot present prompt {config.message!r}: no UI handler installed, "
        "stdin is not a tty, and LITMUS_PROMPT_MODE!=auto-confirm."
    )


def _auto_confirm(config: PromptConfig) -> Any:
    if config.prompt_type == "confirm":
        return True
    if config.prompt_type == "choice" and config.choices:
        return config.choices[0]
    return ""


def _tty_handler(config: PromptConfig) -> Any:
    print(f"\n[Prompt] {config.message}")
    if config.prompt_type == "confirm":
        input("Press Enter to continue...")
        return True
    if config.prompt_type == "choice" and config.choices:
        for i, choice in enumerate(config.choices, 1):
            print(f"  {i}. {choice}")
        while True:
            raw = input("Select option: ")
            try:
                selection = int(raw)
            except ValueError:
                print("Invalid selection, try again.")
                continue
            if 1 <= selection <= len(config.choices):
                return config.choices[selection - 1]
            print("Invalid selection, try again.")
    if config.prompt_type == "input":
        return input("Enter value: ")
    return None
