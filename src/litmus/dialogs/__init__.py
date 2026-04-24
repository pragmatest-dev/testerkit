"""Operator dialog system for test interaction."""

from litmus.dialogs.manager import DialogManager, get_dialog_manager
from litmus.dialogs.models import (
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
]
