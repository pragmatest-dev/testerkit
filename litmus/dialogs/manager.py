"""Dialog manager for coordinating operator interactions."""

import asyncio
import os
from collections.abc import Callable
from uuid import UUID

from litmus.dialogs.models import (
    ChoiceDialog,
    ConfirmDialog,
    Dialog,
    DialogResponse,
    ImageDialog,
    InputDialog,
)


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

    def __init__(self, server_url: str | None = None):
        """Initialize dialog manager.

        Args:
            server_url: If provided, use HTTP mode to communicate with server.
                       If None, uses in-process mode with asyncio events.
        """
        self.server_url = server_url
        self._pending: dict[UUID, Dialog] = {}
        self._responses: dict[UUID, DialogResponse] = {}
        self._events: dict[UUID, asyncio.Event] = {}
        self._listeners: list[Callable[[Dialog], None]] = []

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

    async def show(self, dialog: Dialog) -> DialogResponse:
        """Show a dialog and wait for response.

        Args:
            dialog: The dialog to display.

        Returns:
            The operator's response.
        """
        if self.server_url:
            return await self._show_http(dialog)
        return await self._show_local(dialog)

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
        return self._responses.pop(dialog.id, DialogResponse(dialog_id=dialog.id, cancelled=True))

    def register_dialog(self, dialog: Dialog) -> None:
        """Register a dialog created externally (e.g., from API).

        This adds the dialog to pending without creating an asyncio event,
        since the caller will poll for response via API.
        """
        self._pending[dialog.id] = dialog
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


def get_dialog_manager(server_url: str | None = None) -> DialogManager:
    """Get or create the global dialog manager.

    Args:
        server_url: Optional server URL for HTTP mode. If not provided,
                   checks LITMUS_SERVER_URL environment variable.
                   If neither is set, uses in-process mode.
    """
    global _manager
    if _manager is None:
        # Auto-detect from environment
        url = server_url or os.environ.get("LITMUS_SERVER_URL")
        _manager = DialogManager(server_url=url)
    return _manager
