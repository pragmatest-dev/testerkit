"""Operator dialog system — UI-layer rendering of operator prompts.

Internal to ``testerkit serve``. Test code never imports this module
directly — it goes through :func:`testerkit.prompts.ask`. When the UI is
running, ``register_as_prompt_handler()`` installs a bridge so prompts
route through the dialog queue + UI; otherwise prompts fall back to
terminal / auto-confirm.
"""

from testerkit.api.dialogs.manager import (
    DialogManager,
    get_dialog_manager,
    register_as_prompt_handler,
)
from testerkit.api.dialogs.models import (
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
