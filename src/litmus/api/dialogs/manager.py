"""Dialog manager for coordinating operator interactions."""

import asyncio
import os
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from uuid import UUID

from litmus.api.dialogs.models import (
    ChoiceDialog,
    ConfirmDialog,
    Dialog,
    DialogResponse,
    ImageDialog,
    InputDialog,
)

# Unified auto-confirm env var (shared with litmus.prompts).
# Truthy → auto-confirm all dialogs (confirm-type returns True; choice
# returns first option; input returns ""). Empty / unset → normal flow.
LITMUS_AUTO_CONFIRM = "LITMUS_AUTO_CONFIRM"


class DialogManager:
    """Manages operator dialogs across test runs.

    The dialog manager coordinates between test code requesting dialogs
    and the UI displaying them. It supports two modes:

    1. In-process mode (default): Uses asyncio events for local coordination
    2. HTTP mode: For test subprocesses to communicate with the server

    Usage from test code (in-process):
        manager = get_dialog_manager()
        response = await manager.confirm("Is DUT connected?")
        if response.confirmed:
            # proceed with test

    Usage from test subprocess (HTTP mode):
        manager = get_dialog_manager()  # auto-detects LITMUS_SERVER_URL
        response = await manager.confirm("Is DUT connected?")
        # This POSTs to server and polls for response

    Usage from UI:
        manager = get_dialog_manager()
        dialog = manager.get_pending_dialog(run_id)
        if dialog:
            # display dialog
            manager.respond(dialog.id, DialogResponse(...))
    """

    def __init__(self, server_url: str | None = None, auto_respond: str | None = None):
        """Initialize dialog manager.

        Args:
            server_url: If provided, use HTTP mode to communicate with server.
                       If None, uses in-process mode with asyncio events.
            auto_respond: Auto-respond mode. Values:
                - "confirm": Auto-confirm all dialogs
                - "cancel": Auto-cancel all dialogs
                - None: Check LITMUS_AUTO_CONFIRM env var, then use normal behavior
        """
        self.server_url = server_url
        self._auto_respond = auto_respond
        self._pending: dict[UUID, Dialog] = {}
        self._responses: dict[UUID, DialogResponse] = {}
        self._events: dict[UUID, asyncio.Event] = {}
        self._listeners: list[Callable[[Dialog], None]] = []
        self._preset_responses: deque[DialogResponse] = deque()
        self._open_times: dict[UUID, float] = {}

    def add_listener(self, callback: Callable[[Dialog], None]) -> None:
        """Add a listener for new dialogs."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dialog], None]) -> None:
        """Remove a dialog listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self, dialog: Dialog) -> None:
        """Notify all listeners of a new dialog."""
        for listener in self._listeners:
            try:
                listener(dialog)
            except Exception:
                pass  # Don't let listener errors break dialog flow

    def preset_response(
        self,
        confirmed: bool = True,
        choice: int | None = 0,
        choices: list[int] | None = None,
        value: str = "auto",
        cancelled: bool = False,
    ) -> None:
        """Queue a preset response for the next dialog.

        Preset responses are consumed in FIFO order. Use this in tests
        to avoid waiting for operator input.

        Args:
            confirmed: Whether to confirm the dialog
            choice: Selected choice index (for choice dialogs)
            choices: Selected choice indices (for multi-select)
            value: Input value (for input dialogs)
            cancelled: Whether the dialog was cancelled

        Example:
            manager.preset_response(confirmed=True)
            response = await manager.confirm("Ready?")  # Returns immediately
            assert response.confirmed
        """
        # dialog_id will be set when consumed
        response = DialogResponse(
            dialog_id=UUID("00000000-0000-0000-0000-000000000000"),
            confirmed=confirmed,
            choice=choice,
            choices=choices,
            value=value,
            cancelled=cancelled,
            timed_out=False,
        )
        self._preset_responses.append(response)

    def clear_preset_responses(self) -> None:
        """Clear all queued preset responses."""
        self._preset_responses.clear()

    def _get_auto_response(self, dialog: Dialog) -> DialogResponse | None:
        """Check for auto-respond mode and return immediate response if enabled.

        Checks in order:
        1. Preset responses queue
        2. Instance auto_respond setting
        3. LITMUS_AUTO_CONFIRM environment variable

        Returns:
            DialogResponse if auto-respond is enabled, None otherwise.
        """
        # Check preset responses first
        if self._preset_responses:
            response = self._preset_responses.popleft()
            # Update dialog_id to match actual dialog
            return DialogResponse(
                dialog_id=dialog.id,
                confirmed=response.confirmed,
                choice=response.choice,
                choices=response.choices,
                value=response.value,
                cancelled=response.cancelled,
                timed_out=False,
            )

        # Check instance setting, then environment variable
        auto_mode = self._auto_respond or os.environ.get(LITMUS_AUTO_CONFIRM)
        if not auto_mode:
            return None

        # Generate appropriate auto-response based on mode
        auto_mode = auto_mode.lower()

        if auto_mode == "cancel":
            return DialogResponse(dialog_id=dialog.id, cancelled=True)

        # Default to confirm mode
        return DialogResponse(
            dialog_id=dialog.id,
            confirmed=True,
            choice=0,  # First choice for choice dialogs
            value="auto",  # Default value for input dialogs
        )

    def _emit_dialog_opened(self, dialog: Dialog) -> None:
        """Emit a DialogOpened event if inside a test run.

        Layering note: this method imports from :mod:`litmus.execution`
        lazily and silently no-ops if the import fails. Dialogs do not
        require execution to function — the import is *optional
        enrichment* that fires when a test run is active. Standalone
        UI / API contexts (where dialogs run without a test logger)
        get the dialog itself; the parquet-event side simply doesn't
        light up. Keep this lazy + try/except; do not promote to a
        top-level import.
        """
        try:
            from litmus.execution._state import get_current_logger
        except ImportError:
            return
        logger = get_current_logger()
        if logger is None or logger.event_log is None:
            return
        from litmus.data.events import DialogOpened

        logger.event_log.emit(
            DialogOpened(
                session_id=logger._session_id,
                run_id=logger.test_run.id,
                dialog_id=dialog.id,
                dialog_type=dialog.type.value,
                title=dialog.title,
                message=dialog.message,
                step_name=dialog.step_name,
                blocking=dialog.blocking,
            )
        )

    def _emit_dialog_responded(
        self, dialog: Dialog, response: DialogResponse, duration: float
    ) -> None:
        """Emit a DialogResponded event if inside a test run."""
        try:
            from litmus.execution._state import get_current_logger
        except ImportError:
            return
        logger = get_current_logger()
        if logger is None or logger.event_log is None:
            return
        from litmus.data.events import DialogResponded

        if response.timed_out:
            response_type = "timed_out"
        elif response.cancelled:
            response_type = "cancelled"
        else:
            # Default: any non-cancelled, non-timed-out response is a confirmation
            response_type = "confirmed"

        logger.event_log.emit(
            DialogResponded(
                session_id=logger._session_id,
                run_id=logger.test_run.id,
                dialog_id=dialog.id,
                dialog_type=dialog.type.value,
                response_type=response_type,
                duration_seconds=duration,
                value=response.value,
                choice=response.choice,
            )
        )

    async def show(self, dialog: Dialog) -> DialogResponse:
        """Show a dialog and wait for response.

        Args:
            dialog: The dialog to display.

        Returns:
            The operator's response.
        """
        t0 = time.monotonic()
        self._emit_dialog_opened(dialog)

        # Check for auto-respond mode first
        auto_response = self._get_auto_response(dialog)
        if auto_response is not None:
            self._emit_dialog_responded(dialog, auto_response, time.monotonic() - t0)
            return auto_response

        if self.server_url:
            response = await self._show_http(dialog)
        else:
            response = await self._show_local(dialog)

        self._emit_dialog_responded(dialog, response, time.monotonic() - t0)
        return response

    async def _show_http(self, dialog: Dialog) -> DialogResponse:
        """Show dialog via HTTP (for test subprocesses)."""
        import httpx

        timeout = dialog.timeout_seconds or 300

        async with httpx.AsyncClient(timeout=timeout + 10) as client:
            # POST the dialog to server
            create_resp = await client.post(
                f"{self.server_url}/api/dialogs",
                json={
                    "type": dialog.type.value,
                    "title": dialog.title,
                    "message": dialog.message,
                    "run_id": dialog.run_id,
                    "step_name": dialog.step_name,
                    "timeout_seconds": dialog.timeout_seconds,
                    "choices": getattr(dialog, "choices", None),
                    "allow_multiple": getattr(dialog, "allow_multiple", False),
                    "placeholder": getattr(dialog, "placeholder", ""),
                    "default_value": getattr(dialog, "default_value", ""),
                    "confirm_label": getattr(dialog, "confirm_label", "Confirm"),
                    "cancel_label": getattr(dialog, "cancel_label", "Cancel"),
                },
            )
            create_data = create_resp.json()
            dialog_id = create_data.get("dialog_id", str(dialog.id))

            # Wait for response
            wait_resp = await client.get(
                f"{self.server_url}/api/dialogs/{dialog_id}/wait",
                params={"timeout": timeout},
            )
            resp_data = wait_resp.json()

            return DialogResponse(
                dialog_id=UUID(dialog_id),
                confirmed=resp_data.get("confirmed", False),
                choice=resp_data.get("choice"),
                choices=resp_data.get("choices"),
                value=resp_data.get("value"),
                timed_out=resp_data.get("timed_out", False),
                cancelled=resp_data.get("cancelled", False),
            )

    async def _show_local(self, dialog: Dialog) -> DialogResponse:
        """Show dialog locally (in-process mode)."""
        event = asyncio.Event()
        self._pending[dialog.id] = dialog
        self._events[dialog.id] = event
        self._open_times[dialog.id] = time.monotonic()

        # Notify listeners (UI)
        self._notify_listeners(dialog)

        # Wait for response or timeout
        if dialog.timeout_seconds:
            try:
                await asyncio.wait_for(event.wait(), timeout=dialog.timeout_seconds)
            except TimeoutError:
                response = DialogResponse(dialog_id=dialog.id, timed_out=True)
                self._responses[dialog.id] = response
        else:
            await event.wait()

        # Clean up and return response
        self._pending.pop(dialog.id, None)
        self._events.pop(dialog.id, None)
        self._open_times.pop(dialog.id, None)
        return self._responses.pop(dialog.id, DialogResponse(dialog_id=dialog.id, cancelled=True))

    def register_dialog(self, dialog: Dialog) -> None:
        """Register a dialog created externally (e.g., from API).

        This adds the dialog to pending without creating an asyncio event,
        since the caller will poll for response via API.
        """
        self._pending[dialog.id] = dialog
        self._open_times[dialog.id] = time.monotonic()
        self._emit_dialog_opened(dialog)
        self._notify_listeners(dialog)

    def get_response(self, dialog_id: UUID) -> DialogResponse | None:
        """Get a response for a dialog if available.

        Used by API to check if dialog has been responded to.
        """
        return self._responses.get(dialog_id)

    def respond(self, dialog_id: UUID, response: DialogResponse) -> bool:
        """Submit a response to a pending dialog.

        Args:
            dialog_id: The dialog ID to respond to.
            response: The operator's response.

        Returns:
            True if dialog was found and response recorded.
        """
        if dialog_id not in self._pending:
            return False

        dialog = self._pending[dialog_id]

        # Emit responded event only for externally-registered dialogs
        # (in-process dialogs have events emitted by show())
        if dialog_id not in self._events:
            t0 = self._open_times.pop(dialog_id, None)
            duration = time.monotonic() - t0 if t0 is not None else 0.0
            self._emit_dialog_responded(dialog, response, duration)

        self._responses[dialog_id] = response

        # Signal event if exists (in-process mode)
        event = self._events.get(dialog_id)
        if event:
            event.set()

        # Clean up pending for external dialogs (no event)
        # Keep in _responses for polling
        if dialog_id not in self._events:
            self._pending.pop(dialog_id, None)

        return True

    def get_pending_dialogs(self, run_id: str | None = None) -> list[Dialog]:
        """Get all pending dialogs, optionally filtered by run ID."""
        if run_id is None:
            return list(self._pending.values())
        return [d for d in self._pending.values() if d.run_id == run_id]

    def get_pending_dialog(self, run_id: str | None = None) -> Dialog | None:
        """Get the first pending dialog for a run."""
        dialogs = self.get_pending_dialogs(run_id)
        return dialogs[0] if dialogs else None

    # Convenience methods for common dialog types

    async def confirm(
        self,
        message: str,
        title: str = "Confirm",
        run_id: str | None = None,
        step_name: str | None = None,
        timeout: float | None = None,
    ) -> DialogResponse:
        """Show a confirmation dialog."""
        dialog = ConfirmDialog(
            title=title,
            message=message,
            run_id=run_id,
            step_name=step_name,
            timeout_seconds=timeout,
        )
        return await self.show(dialog)

    async def choose(
        self,
        message: str,
        choices: list[str],
        title: str = "Select",
        run_id: str | None = None,
        step_name: str | None = None,
        allow_multiple: bool = False,
        timeout: float | None = None,
    ) -> DialogResponse:
        """Show a choice selection dialog."""
        dialog = ChoiceDialog(
            title=title,
            message=message,
            choices=choices,
            allow_multiple=allow_multiple,
            run_id=run_id,
            step_name=step_name,
            timeout_seconds=timeout,
        )
        return await self.show(dialog)

    async def input(
        self,
        message: str,
        title: str = "Input",
        placeholder: str = "",
        default_value: str = "",
        run_id: str | None = None,
        step_name: str | None = None,
        timeout: float | None = None,
    ) -> DialogResponse:
        """Show a text input dialog."""
        dialog = InputDialog(
            title=title,
            message=message,
            placeholder=placeholder,
            default_value=default_value,
            run_id=run_id,
            step_name=step_name,
            timeout_seconds=timeout,
        )
        return await self.show(dialog)

    async def show_image(
        self,
        message: str,
        image_url: str | None = None,
        image_path: str | None = None,
        title: str = "Image",
        run_id: str | None = None,
        step_name: str | None = None,
        timeout: float | None = None,
    ) -> DialogResponse:
        """Show an image dialog."""
        dialog = ImageDialog(
            title=title,
            message=message,
            image_url=image_url,
            image_path=image_path,
            run_id=run_id,
            step_name=step_name,
            timeout_seconds=timeout,
        )
        return await self.show(dialog)


# Global dialog manager instance
_manager: DialogManager | None = None


def get_dialog_manager(
    server_url: str | None = None,
    *,
    auto_respond: str | None = None,
) -> DialogManager:
    """Get or create the global dialog manager.

    Args:
        server_url: Optional server URL for HTTP mode. If not provided,
            checks LITMUS_SERVER_URL environment variable. If neither
            is set, uses in-process mode.
        auto_respond: Auto-respond mode (``"confirm"`` / ``"cancel"``).
            Falls back to ``LITMUS_AUTO_CONFIRM`` env var per
            :meth:`DialogManager._get_auto_response`. Only honored on
            first call — subsequent calls return the cached manager.

    Environment variables:
        LITMUS_SERVER_URL: Server URL for HTTP mode
        LITMUS_AUTO_CONFIRM: Truthy → auto-confirm dialogs ("confirm" by
            default; "cancel" / "skip" supported for explicit control)
    """
    global _manager
    if _manager is None:
        url = server_url or os.environ.get("LITMUS_SERVER_URL")
        _manager = DialogManager(server_url=url, auto_respond=auto_respond)
    return _manager


# ---------------------------------------------------------------------------
# Prompt-handler bridge
# ---------------------------------------------------------------------------
#
# A test runner sees one entry point — :func:`litmus.prompts.ask`. When the
# UI / API server is running, that entry point should route through the
# dialog surface instead of the default TTY/auto-confirm chain.
# ``register_as_prompt_handler`` installs the bridge: ``prompts.ask`` from
# the test process becomes a synchronous call that drives the DialogManager
# under a private event loop and translates ``DialogResponse`` back into the
# value :func:`prompts.ask` is expected to return.
#
# Cancellation / timeout surface as :class:`PromptUnavailableError` so the
# caller sees the same failure shape it would from any other prompt path.


def register_as_prompt_handler(server_url: str | None = None) -> None:
    """Install :class:`DialogManager` as the active prompt handler.

    Call this from UI / API startup once the manager is wired up. After
    registration, :func:`litmus.prompts.ask` from the test process
    dispatches through dialogs instead of the TTY / auto-confirm chain.

    Mode requirements:

    * **In-process mode** — caller and DialogManager live in the same
      process (e.g., NiceGUI app driving its own tests). ``server_url``
      can be ``None``; the manager's pending-dialog dict is shared
      directly.
    * **HTTP mode** — caller is a test subprocess and the UI server is a
      separate process. Pass ``server_url`` (or set
      ``LITMUS_SERVER_URL``) so the bridge can POST dialogs to the
      server and poll for the response. Without ``server_url``, the
      test subprocess will silently never resolve its prompts because
      its in-process manager has no UI listener attached.

    Caller is responsible for picking the right mode for its
    deployment; the bridge does not auto-detect.
    """
    from litmus.prompts import set_prompt_handler

    set_prompt_handler(lambda cfg: _dispatch_prompt(cfg, server_url))


def _dispatch_prompt(config: Any, server_url: str | None) -> Any:
    """Sync bridge that runs an async dialog show and unwraps the response."""
    from litmus.prompts import PromptUnavailableError

    manager = get_dialog_manager(server_url)
    if config.prompt_type == "confirm":
        coro = manager.confirm(config.message, timeout=config.timeout_seconds)
    elif config.prompt_type == "choice":
        choices = config.choices or []
        if not choices:
            raise ValueError("choice prompt requires non-empty choices")
        coro = manager.choose(config.message, choices, timeout=config.timeout_seconds)
    elif config.prompt_type == "input":
        coro = manager.input(config.message, timeout=config.timeout_seconds)
    else:
        raise ValueError(f"unknown prompt_type: {config.prompt_type!r}")

    response = _run_sync(coro)
    if response.cancelled or response.timed_out:
        raise PromptUnavailableError(
            f"Dialog for {config.message!r} {'timed out' if response.timed_out else 'cancelled'}."
        )
    if config.prompt_type == "confirm":
        return response.confirmed
    if config.prompt_type == "choice":
        idx = response.choice or 0
        return (config.choices or [])[idx]
    return response.value


def _run_sync(coro: Any) -> Any:
    """Run *coro* to completion from a synchronous context.

    ``asyncio.run`` raises if a loop is already running — a real concern
    if a caller invokes :func:`prompts.ask` from inside an async fixture
    or test. Detect a running loop and surface a clearer error than
    pytest's default ``RuntimeError`` so the caller knows to switch to
    awaiting the manager directly.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop in this thread — safe to run.
        return asyncio.run(coro)

    raise RuntimeError(
        "prompts.ask() was invoked from inside a running event loop. "
        "Async callers should await DialogManager methods directly "
        "(get_dialog_manager().confirm(...) etc.) rather than going "
        "through the sync prompts.ask() bridge."
    )
