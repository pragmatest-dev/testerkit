"""Operator dialog system — UI-layer rendering of operator prompts.

Internal to ``litmus serve``. Test code never imports this module
directly — it goes through :func:`litmus.prompts.ask`. When the UI is
running, ``register_as_prompt_handler()`` installs a bridge so prompts
route through the dialog queue + UI; otherwise prompts fall back to
terminal / auto-confirm.
"""

from litmus.api.dialogs.manager import (
    DialogManager,
    get_dialog_manager,
    register_as_prompt_handler,
)
from litmus.api.dialogs.models import (
    ChoiceDialog,
    ConfirmDialog,
    Dialog,
    DialogResponse,
    DialogType,
    ImageDialog,
    InputDialog,
)

__all__ = [
    "Dialog",
    "DialogType",
    "DialogResponse",
    "ConfirmDialog",
    "ChoiceDialog",
    "InputDialog",
    "ImageDialog",
    "DialogManager",
    "get_dialog_manager",
    "register_as_prompt_handler",
]
