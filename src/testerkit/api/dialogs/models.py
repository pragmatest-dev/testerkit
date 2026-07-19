"""Dialog models for operator interaction."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DialogType(StrEnum):
    """Types of operator dialogs."""

    CONFIRM = "confirm"  # Yes/No confirmation
    CHOICE = "choice"  # Select from options
    INPUT = "input"  # Free-form text input
    IMAGE = "image"  # Display image, optional input


class Dialog(BaseModel):
    """Base dialog model."""

    id: UUID = Field(default_factory=uuid4)
    type: DialogType
    title: str
    message: str
    run_id: str | None = None  # Associated test run
    step_name: str | None = None  # Associated test step
    timeout_seconds: float | None = None  # Auto-dismiss after timeout
    blocking: bool = True  # Whether test waits for response


class ConfirmDialog(Dialog):
    """Yes/No confirmation dialog."""

    type: DialogType = DialogType.CONFIRM
    confirm_label: str = "Confirm"
    cancel_label: str = "Cancel"
    default_confirm: bool = True  # Which button is default


class ChoiceDialog(Dialog):
    """Multiple choice selection dialog."""

    type: DialogType = DialogType.CHOICE
    choices: list[str]
    allow_multiple: bool = False
    default_choice: int | None = None  # Index of default choice


class InputDialog(Dialog):
    """Free-form text input dialog."""

    type: DialogType = DialogType.INPUT
    placeholder: str = ""
    default_value: str = ""
    validation_pattern: str | None = None  # Regex for validation
    input_type: str = "text"  # text, number, password


class ImageDialog(Dialog):
    """Display image with optional confirmation."""

    type: DialogType = DialogType.IMAGE
    image_url: str | None = None  # URL or base64 data URI
    image_path: str | None = None  # Local file path
    show_confirm: bool = True  # Show confirm/cancel buttons
    capture_enabled: bool = False  # Allow capturing new image


class DialogResponse(BaseModel):
    """Response from an operator dialog."""

    dialog_id: UUID
    confirmed: bool = False  # For confirm dialogs
    choice: int | None = None  # Index for choice dialogs
    choices: list[int] | None = None  # Indices for multi-select
    value: str | None = None  # For input dialogs
    image_data: str | None = None  # Base64 for captured images
    timed_out: bool = False  # Whether dialog timed out
    cancelled: bool = False  # Whether operator cancelled
